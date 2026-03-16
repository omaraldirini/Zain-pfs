from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ZainLoan(models.Model):
    """Personal loan from the Provident Fund.
    Workflow: draft → submitted → preparation → approval_1 → approval_2
              → pending_payment → active → paid
    """
    _name = 'zain.loan'
    _description = 'PFS Loan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='restrict', tracking=True,
    )
    date = fields.Date(string='Application Date', default=fields.Date.today, tracking=True)

    # ── Application fields ───────────────────────────────────────────────────
    loan_amount = fields.Float(string='Loan Amount (JOD)', required=True, tracking=True)
    installments = fields.Integer(string='Installments (months)', required=True)
    other_income = fields.Float(string='Other Income (JOD)')
    bank_installment = fields.Float(
        string='Bank Installment (JOD)',
        help='External bank installment already committed by the member.',
    )
    notes = fields.Text(string='Notes')
    is_rescheduled = fields.Boolean(string='Is Buyout / Reschedule', tracking=True)

    # ── Computed financial fields ────────────────────────────────────────────
    admin_fees = fields.Float(string='Admin Fees (JOD)', compute='_compute_financials', store=True)
    monthly_installment = fields.Float(
        string='Monthly Installment', compute='_compute_financials', store=True,
    )
    first_installment = fields.Float(
        string='First Installment', compute='_compute_financials', store=True,
        help='Adjusted to absorb rounding; all subsequent installments are equal whole numbers.',
    )
    max_allowed_amount = fields.Float(
        string='Max Allowed Amount', compute='_compute_max_allowed', store=False,
    )
    remaining_balance = fields.Float(
        string='Remaining Balance', compute='_compute_remaining_balance', store=True,
    )
    total_loan_amount = fields.Float(
        string='Total Loan + Fees', compute='_compute_financials', store=True,
    )

    # ── Payment / disbursement ───────────────────────────────────────────────
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Payment Method', tracking=True)
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount')

    # ── Workflow state ───────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted (HR)'),
        ('preparation', 'Preparation'),
        ('approval_1', 'Approval #1 (Treasurer)'),
        ('approval_2', 'Approval #2 (Committee Head)'),
        ('pending_payment', 'Pending Payment'),
        ('active', 'Active'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string='Status', tracking=True)

    # ── HR data (filled during Submitted stage) ──────────────────────────────
    average_income = fields.Float(
        string='Average Income (last 3 months)',
        tracking=True,
        help='Filled by HR during review.',
    )

    # ── Repayment schedule ───────────────────────────────────────────────────
    line_ids = fields.One2many('zain.loan.line', 'loan_id', string='Repayment Schedule')

    # ── Lifecycle ────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.loan') or 'New'
        return super().create(vals_list)

    # ── Computed fields ──────────────────────────────────────────────────────
    @api.depends('loan_amount', 'installments', 'is_rescheduled')
    def _compute_financials(self):
        config = self.env['zain.configuration'].search([], limit=1)
        fee_threshold = config.loan_admin_fees_threshold if config else 504.0
        standard_fee = config.loan_admin_fees if config else 5.0
        reschedule_fee = config.reschedule_fees if config else 25.0

        for rec in self:
            if rec.is_rescheduled:
                rec.admin_fees = reschedule_fee
            elif rec.loan_amount > fee_threshold:
                rec.admin_fees = standard_fee
            else:
                rec.admin_fees = 0.0

            rec.total_loan_amount = rec.loan_amount + rec.admin_fees

            if rec.installments > 0 and rec.loan_amount > 0:
                raw = rec.total_loan_amount / rec.installments
                base = int(raw)  # whole-number installment
                remainder = rec.total_loan_amount - base * rec.installments
                rec.monthly_installment = base
                rec.first_installment = round(base + remainder, 3)
            else:
                rec.monthly_installment = 0.0
                rec.first_installment = 0.0

    @api.depends('member_id.current_balance', 'average_income', 'bank_installment',
                 'installments')
    def _compute_max_allowed(self):
        config = self.env['zain.configuration'].search([], limit=1)
        cap = config.loan_installment_cap if config else 0.5
        for rec in self:
            avg = rec.average_income or 0.0
            max_installment = avg * cap - (rec.bank_installment or 0.0)
            if max_installment > 0 and rec.installments > 0:
                rec.max_allowed_amount = min(
                    max_installment * rec.installments,
                    rec.member_id.current_balance,
                )
            else:
                rec.max_allowed_amount = 0.0

    @api.depends('total_loan_amount', 'line_ids.paid', 'line_ids.amount')
    def _compute_remaining_balance(self):
        for rec in self:
            paid = sum(rec.line_ids.filtered('paid').mapped('amount'))
            rec.remaining_balance = max(0.0, rec.total_loan_amount - paid)

    # ── Eligibility validation ────────────────────────────────────────────────
    @api.constrains('member_id', 'loan_amount')
    def _check_eligibility(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_months = config.min_contribution_months if config else 36
        for rec in self:
            if rec.member_id.contribution_months < min_months:
                raise ValidationError(_(
                    'Member %s has only %d contribution months. '
                    'Minimum required: %d months.'
                ) % (rec.member_id.employee_id.name,
                     rec.member_id.contribution_months,
                     min_months))

    # ── Workflow buttons ──────────────────────────────────────────────────────
    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_to_preparation(self):
        self.write({'state': 'preparation'})

    def action_approval_1(self):
        self.write({'state': 'approval_1'})

    def action_approval_2(self):
        self.write({'state': 'approval_2'})

    def action_pending_payment(self):
        self.write({'state': 'pending_payment'})

    def action_disburse(self):
        """Mark as Active and generate repayment schedule."""
        for rec in self:
            if not rec.line_ids:
                rec._generate_schedule()
            rec.state = 'active'

    def action_mark_paid(self):
        self.write({'state': 'paid'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    # ── Schedule generation ───────────────────────────────────────────────────
    def _generate_schedule(self):
        self.ensure_one()
        self.line_ids.unlink()
        lines = []
        start = self.date or fields.Date.today()
        from dateutil.relativedelta import relativedelta
        for i in range(self.installments):
            due = start + relativedelta(months=i + 1)
            amount = self.first_installment if i == 0 else self.monthly_installment
            lines.append({
                'loan_id': self.id,
                'sequence': i + 1,
                'date': due,
                'amount': amount,
                'paid': False,
            })
        self.env['zain.loan.line'].create(lines)


class ZainLoanLine(models.Model):
    _name = 'zain.loan.line'
    _description = 'Loan Repayment Schedule Line'
    _order = 'sequence'

    loan_id = fields.Many2one('zain.loan', required=True, ondelete='cascade')
    sequence = fields.Integer(default=1)
    date = fields.Date(string='Due Date')
    amount = fields.Float(string='Installment Amount')
    paid = fields.Boolean(string='Paid', default=False)
    payment_date = fields.Date(string='Payment Date')
