from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ZainLandLoan(models.Model):
    """Land-specific loan tracked separately from personal loans (BRD §5.3.3).
    Workflow: draft → submitted → preparation → approval_1 → approval_2
              → pending_payment → active → paid
    """
    _name = 'zain.land.loan'
    _description = 'PFS Land Loan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='restrict', tracking=True,
    )
    date = fields.Date(string='Application Date', default=fields.Date.today, tracking=True)

    # ── Land details ──────────────────────────────────────────────────────────
    land_id = fields.Many2one(
        'zain.land', string='Land Plot', required=True,
        domain="[('status', 'in', ('available', 'reserved'))]",
        tracking=True,
    )
    land_price = fields.Float(
        related='land_id.price', string='Land Price (JOD)', store=True, readonly=True,
    )
    land_area = fields.Float(
        related='land_id.area', string='Area (sq m)', readonly=True,
    )
    land_location = fields.Char(
        related='land_id.location', string='Location', readonly=True,
    )

    # ── Financing ─────────────────────────────────────────────────────────────
    down_payment = fields.Float(string='Down Payment (JOD)', tracking=True)
    installments = fields.Integer(string='Installments (months)', tracking=True)
    financed_amount = fields.Float(
        string='Financed Amount (JOD)',
        compute='_compute_installment', store=True,
    )
    installment_amount = fields.Float(
        string='Monthly Installment (JOD)',
        compute='_compute_installment', store=True,
    )
    first_installment = fields.Float(
        string='First Installment (JOD)',
        compute='_compute_installment', store=True,
        help='Absorbs rounding remainder; subsequent installments are equal.',
    )
    paid_amount = fields.Float(
        string='Paid Amount (JOD)',
        compute='_compute_remaining_balance', store=True,
    )
    remaining_balance = fields.Float(
        string='Remaining Balance (JOD)',
        compute='_compute_remaining_balance', store=True,
    )
    overdue_count = fields.Integer(
        string='Overdue Installments',
        compute='_compute_overdue', store=False,
    )

    # ── Member summary ────────────────────────────────────────────────────────
    member_balance = fields.Float(
        related='member_id.current_balance', string='Member Balance (JOD)', readonly=True,
    )
    member_contribution_months = fields.Integer(
        related='member_id.contribution_months', string='Contribution Months', readonly=True,
    )

    # ── Payment / disbursement ────────────────────────────────────────────────
    disbursement_date = fields.Date(string='Disbursement Date', readonly=True)
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Payment Method', tracking=True)
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount (JOD)')

    # ── State ─────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted (HR)'),
        ('preparation', 'Preparation'),
        ('approval_1', 'Approval #1'),
        ('approval_2', 'Approval #2'),
        ('pending_payment', 'Pending Payment'),
        ('active', 'Active'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string='Status', tracking=True)

    line_ids = fields.One2many('zain.land.loan.line', 'loan_id', string='Repayment Schedule')
    notes = fields.Text()

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.land.loan') or 'New'
        return super().create(vals_list)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('land_price', 'down_payment', 'installments')
    def _compute_installment(self):
        for rec in self:
            financed = max(0.0, (rec.land_price or 0.0) - (rec.down_payment or 0.0))
            rec.financed_amount = financed
            if rec.installments and financed > 0:
                base = int(financed / rec.installments)
                remainder = financed - base * rec.installments
                rec.installment_amount = base
                rec.first_installment = round(base + remainder, 3)
            else:
                rec.installment_amount = 0.0
                rec.first_installment = 0.0

    @api.depends('financed_amount', 'line_ids.paid', 'line_ids.amount')
    def _compute_remaining_balance(self):
        for rec in self:
            paid = sum(rec.line_ids.filtered('paid').mapped('amount'))
            rec.paid_amount = paid
            rec.remaining_balance = max(0.0, rec.financed_amount - paid)

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
    # Workflow buttons
    # ─────────────────────────────────────────────────────────────────────────

    def action_submit(self):
        for rec in self:
            if rec.down_payment < 0:
                raise UserError(_('Down payment cannot be negative.'))
            if rec.installments <= 0:
                raise UserError(_('Number of installments must be greater than zero.'))
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
        for rec in self:
            if not rec.payment_method:
                raise UserError(_('Please set a Payment Method before disbursing.'))
            if rec.payment_method == 'cheque' and not rec.cheque_number:
                raise UserError(_('Please enter the Cheque Number before disbursing.'))
            if not rec.line_ids:
                rec._generate_schedule()
            rec.land_id.action_reserve(rec.member_id.id)
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
            rec.land_id.action_mark_sold()
            rec.state = 'paid'

    def action_cancel(self):
        for rec in self:
            # Release plot reservation if still reserved for this member
            if (rec.land_id.status == 'reserved'
                    and rec.land_id.reserved_member_id == rec.member_id):
                rec.land_id.write({
                    'status': 'available',
                    'reserved_member_id': False,
                    'reserved_date': False,
                })
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
            amount = self.first_installment if i == 0 else self.installment_amount
            lines.append({
                'loan_id': self.id,
                'sequence': i + 1,
                'date': due,
                'amount': amount,
            })
        self.env['zain.land.loan.line'].create(lines)


class ZainLandLoanLine(models.Model):
    _name = 'zain.land.loan.line'
    _description = 'Land Loan Repayment Schedule Line'
    _order = 'sequence'

    loan_id = fields.Many2one('zain.land.loan', required=True, ondelete='cascade')
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
