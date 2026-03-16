from odoo import api, fields, models, _
from dateutil.relativedelta import relativedelta


class ZainLandLoan(models.Model):
    """Land-specific loan.  Tracked separately from personal loans per BRD §5.3.3.
    Workflow mirrors personal loans.
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
    date = fields.Date(string='Application Date', default=fields.Date.today)

    # ── Land details ──────────────────────────────────────────────────────────
    land_id = fields.Many2one(
        'zain.land', string='Land Plot', required=True,
        domain=[('status', '=', 'available')],
    )
    land_price = fields.Float(related='land_id.price', string='Land Price (JOD)', store=True)
    down_payment = fields.Float(string='Down Payment (JOD)')
    installments = fields.Integer(string='Installments (months)')
    installment_amount = fields.Float(
        string='Monthly Installment', compute='_compute_installment', store=True,
    )
    remaining_balance = fields.Float(
        string='Remaining Balance', compute='_compute_remaining_balance', store=True,
    )

    # ── Payment / disbursement ────────────────────────────────────────────────
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Payment Method')
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount')

    # ── State ────────────────────────────────────────────────────────────────
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

    # ── Sequence ──────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.land.loan') or 'New'
        return super().create(vals_list)

    # ── Computed ──────────────────────────────────────────────────────────────
    @api.depends('land_price', 'down_payment', 'installments')
    def _compute_installment(self):
        for rec in self:
            financed = (rec.land_price or 0.0) - (rec.down_payment or 0.0)
            if rec.installments and financed > 0:
                rec.installment_amount = round(financed / rec.installments, 3)
            else:
                rec.installment_amount = 0.0

    @api.depends('land_price', 'down_payment', 'line_ids.paid', 'line_ids.amount')
    def _compute_remaining_balance(self):
        for rec in self:
            total = (rec.land_price or 0.0) - (rec.down_payment or 0.0)
            paid = sum(rec.line_ids.filtered('paid').mapped('amount'))
            rec.remaining_balance = max(0.0, total - paid)

    # ── Workflow ──────────────────────────────────────────────────────────────
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
        for rec in self:
            if not rec.line_ids:
                rec._generate_schedule()
            # Reserve the land plot
            rec.land_id.write({
                'status': 'reserved',
                'reserved_member_id': rec.member_id.id,
            })
            rec.state = 'active'

    def action_mark_paid(self):
        for rec in self:
            rec.land_id.action_mark_sold()
            rec.state = 'paid'

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def _generate_schedule(self):
        self.ensure_one()
        self.line_ids.unlink()
        start = self.date or fields.Date.today()
        lines = []
        for i in range(self.installments):
            due = start + relativedelta(months=i + 1)
            lines.append({
                'loan_id': self.id,
                'sequence': i + 1,
                'date': due,
                'amount': self.installment_amount,
            })
        self.env['zain.land.loan.line'].create(lines)


class ZainLandLoanLine(models.Model):
    _name = 'zain.land.loan.line'
    _description = 'Land Loan Repayment Schedule Line'
    _order = 'sequence'

    loan_id = fields.Many2one('zain.land.loan', required=True, ondelete='cascade')
    sequence = fields.Integer(default=1)
    date = fields.Date(string='Due Date')
    amount = fields.Float(string='Installment Amount')
    paid = fields.Boolean(default=False)
    payment_date = fields.Date(string='Payment Date')
