from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class ZainWithdrawal(models.Model):
    """Partial withdrawal from the Provident Fund.
    Supports 50% and 75% withdrawal types per BRD §5.5.
    Workflow: draft → hr_review → approval_1 → approval_2 → approved
    """
    _name = 'zain.withdrawal'
    _description = 'PFS Withdrawal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='restrict', tracking=True,
    )
    date = fields.Date(string='Request Date', default=fields.Date.today)
    withdrawal_type = fields.Selection([
        ('50', '50% Withdrawal'),
        ('75', '75% Withdrawal'),
    ], string='Withdrawal Type', required=True, tracking=True)
    requested_amount = fields.Float(string='Requested Amount (JOD)', required=True)
    eligible_amount = fields.Float(
        string='Eligible Amount', compute='_compute_eligible_amount', store=False,
    )
    net_amount = fields.Float(
        string='Net Amount', compute='_compute_net_amount', store=False,
        help='Final amount after settling any outstanding loans.',
    )
    is_loan_settlement = fields.Boolean(
        string='Loan Settled from Withdrawal',
        help='If member has an active loan, it is settled first from the withdrawal.',
    )
    approved_amount = fields.Float(string='Approved Amount', readonly=True)
    approved_date = fields.Date(string='Approval Date', readonly=True)
    notes = fields.Text(string='Notes')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('hr_review', 'HR Review'),
        ('approval_1', 'Committee Approval #1'),
        ('approval_2', 'Committee Approval #2'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string='Status', tracking=True)

    # ── Sequence ──────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.withdrawal') or 'New'
        return super().create(vals_list)

    # ── Computed ──────────────────────────────────────────────────────────────
    @api.depends('member_id', 'withdrawal_type')
    def _compute_eligible_amount(self):
        for rec in self:
            if rec.withdrawal_type == '50':
                rec.eligible_amount = rec.member_id.eligibility_50
            elif rec.withdrawal_type == '75':
                rec.eligible_amount = rec.member_id.eligibility_75
            else:
                rec.eligible_amount = 0.0

    @api.depends('eligible_amount', 'requested_amount', 'member_id.loan_balance')
    def _compute_net_amount(self):
        for rec in self:
            effective = min(rec.requested_amount, rec.eligible_amount)
            outstanding_loan = rec.member_id.loan_balance if rec.is_loan_settlement else 0.0
            rec.net_amount = max(0.0, effective - outstanding_loan)

    # ── Auto-select withdrawal type based on contribution months ──────────────
    @api.onchange('member_id')
    def _onchange_member_id(self):
        if self.member_id:
            config = self.env['zain.configuration'].search([], limit=1)
            min_75 = config.withdrawal_75_min_months if config else 240
            min_50 = config.withdrawal_50_min_months if config else 60
            months = self.member_id.contribution_months
            if months >= min_75:
                self.withdrawal_type = '75'
            elif months >= min_50:
                self.withdrawal_type = '50'
            else:
                self.withdrawal_type = False

    # ── Validation ────────────────────────────────────────────────────────────
    @api.constrains('member_id', 'withdrawal_type', 'requested_amount')
    def _check_eligibility(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_50 = config.withdrawal_50_min_months if config else 60
        min_75 = config.withdrawal_75_min_months if config else 240
        for rec in self:
            months = rec.member_id.contribution_months
            if rec.withdrawal_type == '50' and months < min_50:
                raise ValidationError(_(
                    'Member has %d contribution months. '
                    'Minimum for 50%% withdrawal: %d months.'
                ) % (months, min_50))
            if rec.withdrawal_type == '75' and months < min_75:
                raise ValidationError(_(
                    'Member has %d contribution months. '
                    'Minimum for 75%% withdrawal: %d months.'
                ) % (months, min_75))
            # Check for existing active withdrawal request
            existing = self.search([
                ('member_id', '=', rec.member_id.id),
                ('state', 'not in', ('approved', 'cancelled')),
                ('id', '!=', rec.id),
            ])
            if existing:
                raise ValidationError(_(
                    'Member already has an active withdrawal request (%s).'
                ) % existing[0].name)

    # ── Workflow buttons ──────────────────────────────────────────────────────
    def action_submit(self):
        self.write({'state': 'hr_review'})

    def action_to_approval_1(self):
        self.write({'state': 'approval_1'})

    def action_to_approval_2(self):
        self.write({'state': 'approval_2'})

    def action_approve(self):
        for rec in self:
            rec.approved_amount = min(rec.requested_amount, rec.eligible_amount)
            rec.approved_date = fields.Date.today()
            rec.state = 'approved'

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
