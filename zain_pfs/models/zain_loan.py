from dateutil.relativedelta import relativedelta

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

    # ── Application fields ────────────────────────────────────────────────────
    loan_amount = fields.Float(string='Loan Amount (JOD)', required=True, tracking=True)
    installments = fields.Integer(string='Installments (months)', required=True)
    other_income = fields.Float(
        string='Other Income (JOD)',
        help='Additional income sources used for repayment capacity calculation.',
    )
    bank_installment = fields.Float(
        string='External Bank Installment (JOD)',
        help='Monthly commitment to external bank – counts against the 50% cap.',
    )
    notes = fields.Text(string='Notes')
    is_rescheduled = fields.Boolean(string='Is Buyout / Reschedule', tracking=True)
    original_loan_id = fields.Many2one(
        'zain.loan', string='Original Loan',
        domain="[('member_id','=',member_id),('state','=','active')]",
        help='Populate when this loan is a buyout/reschedule of an existing loan.',
    )

    # ── HR data (filled during Submitted stage) ───────────────────────────────
    average_income = fields.Float(
        string='Average Income – last 3 months (JOD)',
        tracking=True,
        help='Filled by HR during review.',
    )

    # ── Computed financial fields ─────────────────────────────────────────────
    admin_fees = fields.Float(
        string='Admin Fees (JOD)',
        compute='_compute_financials', store=True,
    )
    total_loan_amount = fields.Float(
        string='Total (Loan + Fees)',
        compute='_compute_financials', store=True,
    )
    monthly_installment = fields.Float(
        string='Monthly Installment (JOD)',
        compute='_compute_financials', store=True,
    )
    first_installment = fields.Float(
        string='First Installment (JOD)',
        compute='_compute_financials', store=True,
        help='Absorbs rounding remainder; all subsequent installments are equal whole numbers.',
    )
    max_allowed_amount = fields.Float(
        string='Max Allowed Amount (JOD)',
        compute='_compute_max_allowed', store=False,
    )
    remaining_balance = fields.Float(
        string='Remaining Balance (JOD)',
        compute='_compute_remaining_balance', store=True,
    )
    paid_amount = fields.Float(
        string='Paid Amount (JOD)',
        compute='_compute_remaining_balance', store=True,
    )
    overdue_count = fields.Integer(
        string='Overdue Installments',
        compute='_compute_overdue', store=False,
    )

    # ── Member summary (read-only, for display on the form) ───────────────────
    member_contribution_months = fields.Integer(
        related='member_id.contribution_months', string='Contribution Months', readonly=True,
    )
    member_balance = fields.Float(
        related='member_id.current_balance', string='Member Balance', readonly=True,
    )
    member_eligibility_50 = fields.Float(
        related='member_id.eligibility_50', string='Eligibility (50%)', readonly=True,
    )

    # ── Payment / disbursement ────────────────────────────────────────────────
    disbursement_date = fields.Date(string='Disbursement Date', readonly=True)
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Payment Method', tracking=True)
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount')

    # ── Workflow state ────────────────────────────────────────────────────────
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

    # ── Repayment schedule ────────────────────────────────────────────────────
    line_ids = fields.One2many('zain.loan.line', 'loan_id', string='Repayment Schedule')

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.loan') or 'New'
        return super().create(vals_list)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('loan_amount', 'installments', 'is_rescheduled')
    def _compute_financials(self):
        config = self.env['zain.configuration'].search([], limit=1)
        fee_threshold = config.loan_admin_fees_threshold if config else 504.0
        standard_fee = config.loan_admin_fees if config else 5.0
        reschedule_fee = config.reschedule_fees if config else 25.0

        for rec in self:
            # Admin fees
            if rec.is_rescheduled:
                rec.admin_fees = reschedule_fee
            elif rec.loan_amount > fee_threshold:
                rec.admin_fees = standard_fee
            else:
                rec.admin_fees = 0.0

            total = rec.loan_amount + rec.admin_fees
            rec.total_loan_amount = total

            if rec.installments > 0 and total > 0:
                base = int(total / rec.installments)      # whole-number installment
                remainder = total - base * rec.installments
                rec.monthly_installment = base
                rec.first_installment = round(base + remainder, 3)
            else:
                rec.monthly_installment = 0.0
                rec.first_installment = 0.0

    @api.depends(
        'member_id.current_balance',
        'member_id.loan_ids.state',
        'member_id.loan_ids.monthly_installment',
        'average_income',
        'bank_installment',
        'installments',
    )
    def _compute_max_allowed(self):
        config = self.env['zain.configuration'].search([], limit=1)
        cap = config.loan_installment_cap if config else 0.5

        for rec in self:
            avg = rec.average_income or 0.0

            # Sum other active loans' monthly installments (excluding this record)
            other_installments = sum(
                l.monthly_installment
                for l in rec.member_id.loan_ids
                if l.state == 'active' and l.id != rec.id
            )

            # Available monthly capacity
            max_monthly = (avg * cap) - (rec.bank_installment or 0.0) - other_installments

            if max_monthly > 0 and rec.installments > 0:
                max_by_income = max_monthly * rec.installments
                max_by_balance = rec.member_id.current_balance
                rec.max_allowed_amount = min(max_by_income, max_by_balance)
            else:
                rec.max_allowed_amount = 0.0

    @api.depends('total_loan_amount', 'line_ids.paid', 'line_ids.amount')
    def _compute_remaining_balance(self):
        for rec in self:
            paid = sum(rec.line_ids.filtered('paid').mapped('amount'))
            rec.paid_amount = paid
            rec.remaining_balance = max(0.0, rec.total_loan_amount - paid)

    @api.depends('line_ids.paid', 'line_ids.date', 'state')
    def _compute_overdue(self):
        today = fields.Date.today()
        for rec in self:
            if rec.state != 'active':
                rec.overdue_count = 0
            else:
                rec.overdue_count = len(
                    rec.line_ids.filtered(lambda l: not l.paid and l.date and l.date < today)
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Constraints
    # ─────────────────────────────────────────────────────────────────────────

    @api.constrains('member_id')
    def _check_member_eligibility(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_months = config.min_contribution_months if config else 36
        for rec in self:
            if rec.state == 'draft':
                continue
            if rec.member_id.contribution_months < min_months:
                raise ValidationError(_(
                    'Member "%s" has only %d contribution months. '
                    'Minimum required: %d months.'
                ) % (rec.member_id.employee_id.name,
                     rec.member_id.contribution_months,
                     min_months))

    def _validate_installment_cap(self):
        """Raise if (this loan's installment + bank installment + other active loans)
        exceeds the configured % of average income."""
        config = self.env['zain.configuration'].search([], limit=1)
        cap = config.loan_installment_cap if config else 0.5
        for rec in self:
            if not rec.average_income:
                continue
            other_installments = sum(
                l.monthly_installment
                for l in rec.member_id.loan_ids
                if l.state == 'active' and l.id != rec.id
            )
            total_monthly = rec.monthly_installment + (rec.bank_installment or 0.0) + other_installments
            cap_amount = rec.average_income * cap
            if total_monthly > cap_amount:
                raise ValidationError(_(
                    'Total monthly deductions (%.3f JOD) exceed %.0f%% of average income '
                    '(%.3f JOD). Maximum allowed installment: %.3f JOD.'
                ) % (total_monthly, cap * 100, rec.average_income, cap_amount))

    # ─────────────────────────────────────────────────────────────────────────
    # Workflow buttons
    # ─────────────────────────────────────────────────────────────────────────

    def action_submit(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_months = config.min_contribution_months if config else 36
        for rec in self:
            if rec.member_id.contribution_months < min_months:
                raise UserError(_(
                    'Cannot submit: member "%s" has only %d contribution months '
                    '(minimum: %d).'
                ) % (rec.member_id.employee_id.name,
                     rec.member_id.contribution_months,
                     min_months))
        self.write({'state': 'submitted'})

    def action_to_preparation(self):
        self.write({'state': 'preparation'})

    def action_approval_1(self):
        self._validate_installment_cap()
        self.write({'state': 'approval_1'})

    def action_approval_2(self):
        self.write({'state': 'approval_2'})

    def action_pending_payment(self):
        self.write({'state': 'pending_payment'})

    def action_disburse(self):
        for rec in self:
            if not rec.payment_method:
                raise UserError(_('Please set a Payment Method before disbursing.'))
            if rec.payment_method == 'cheque' and not rec.cheque_number:
                raise UserError(_('Please enter the Cheque Number before disbursing.'))
            if not rec.line_ids:
                rec._generate_schedule()
            rec.disbursement_date = fields.Date.today()
            rec.state = 'active'

    def action_mark_paid(self):
        for rec in self:
            unpaid = rec.line_ids.filtered(lambda l: not l.paid)
            if unpaid:
                raise UserError(_(
                    '%d installment(s) are still unpaid. '
                    'Mark all lines as paid before closing the loan.'
                ) % len(unpaid))
        self.write({'state': 'paid'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    # ─────────────────────────────────────────────────────────────────────────
    # Schedule generation
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_schedule(self):
        self.ensure_one()
        self.line_ids.unlink()
        start = self.date or fields.Date.today()
        lines = []
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
    amount = fields.Float(string='Installment Amount (JOD)')
    paid = fields.Boolean(string='Paid', default=False)
    payment_date = fields.Date(string='Payment Date')

    @api.onchange('paid')
    def _onchange_paid(self):
        if self.paid and not self.payment_date:
            self.payment_date = fields.Date.today()
        elif not self.paid:
            self.payment_date = False
