from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ZainWithdrawal(models.Model):
    """Partial withdrawal from the Provident Fund.

    Supports 50% and 75% withdrawal types per BRD §5.5.

    Key lock-period rules (50% type):
    - Cash withdrawal  → locked 5 years from approval date
    - Loan settlement  → locked 3 years from approval date
    - After first withdrawal, subsequent 50% eligibility uses the balance
      at the time of the first withdrawal as the base (not current balance).

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
    date = fields.Date(string='Request Date', default=fields.Date.today, tracking=True)

    # ── Request ───────────────────────────────────────────────────────────────
    withdrawal_type = fields.Selection([
        ('50', '50% Withdrawal'),
        ('75', '75% Withdrawal'),
    ], string='Withdrawal Type', required=True, tracking=True)
    requested_amount = fields.Float(string='Requested Amount (JOD)', required=True)

    # ── Eligibility (computed from member, not stored) ────────────────────────
    member_contribution_months = fields.Integer(
        related='member_id.contribution_months',
        string='Contribution Months', readonly=True,
    )
    member_balance = fields.Float(
        related='member_id.current_balance',
        string='Current Balance (JOD)', readonly=True,
    )
    eligible_amount = fields.Float(
        string='Eligible Amount (JOD)',
        compute='_compute_eligible_amount', store=False,
    )

    # ── Loan settlement (automatic per BRD §5.5.5) ───────────────────────────
    is_loan_settlement = fields.Boolean(
        string='Loan Settled from Withdrawal',
        compute='_compute_is_loan_settlement', store=True,
        help='Automatically True when member has active personal loans. '
             'Loan balance is settled first; remainder goes to member.',
    )
    loan_settlement_amount = fields.Float(
        string='Loan Settlement Amount (JOD)',
        compute='_compute_amounts', store=False,
        help='Amount deducted to settle outstanding personal loans.',
    )
    cash_amount = fields.Float(
        string='Cash to Member (JOD)',
        compute='_compute_amounts', store=False,
        help='Amount disbursed in cash after settling any loans.',
    )
    net_amount = fields.Float(
        string='Net Amount (JOD)',
        compute='_compute_amounts', store=False,
        help='Effective withdrawal amount (capped at eligible amount).',
    )

    # ── Lock period ───────────────────────────────────────────────────────────
    lock_years = fields.Integer(
        string='Lock Period (years)',
        compute='_compute_lock', store=True,
        help='3 years for loan settlement; 5 years for cash withdrawal.',
    )
    lock_expiry_date = fields.Date(
        string='Lock Expires On',
        compute='_compute_lock', store=True,
    )

    # ── Approval ──────────────────────────────────────────────────────────────
    approved_amount = fields.Float(string='Approved Amount (JOD)', readonly=True, tracking=True)
    approved_date = fields.Date(string='Approval Date', readonly=True)
    member_balance_at_approval = fields.Float(
        string='Balance at Approval (JOD)', readonly=True,
        help='Member balance frozen at the moment of approval. '
             'Used as the base for future 50% eligibility calculations.',
    )

    # ── Disbursement ──────────────────────────────────────────────────────────
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Disbursement Method', tracking=True)
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount')

    notes = fields.Text(string='Notes')

    # ── Workflow state ────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('hr_review', 'HR Review'),
        ('approval_1', 'Committee Approval #1'),
        ('approval_2', 'Committee Approval #2'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string='Status', tracking=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.withdrawal') or 'New'
        return super().create(vals_list)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('member_id', 'withdrawal_type')
    def _compute_eligible_amount(self):
        for rec in self:
            if rec.withdrawal_type == '50':
                rec.eligible_amount = rec.member_id.eligibility_50
            elif rec.withdrawal_type == '75':
                rec.eligible_amount = rec.member_id.eligibility_75
            else:
                rec.eligible_amount = 0.0

    @api.depends('member_id.loan_balance', 'withdrawal_type')
    def _compute_is_loan_settlement(self):
        for rec in self:
            rec.is_loan_settlement = (
                rec.withdrawal_type == '50'
                and rec.member_id.loan_balance > 0
            )

    @api.depends(
        'requested_amount',
        'eligible_amount',
        'is_loan_settlement',
        'member_id.loan_balance',
    )
    def _compute_amounts(self):
        for rec in self:
            effective = min(rec.requested_amount, rec.eligible_amount)
            rec.net_amount = effective

            if rec.is_loan_settlement:
                loan_bal = rec.member_id.loan_balance
                settled = min(loan_bal, effective)
                rec.loan_settlement_amount = settled
                rec.cash_amount = max(0.0, effective - settled)
            else:
                rec.loan_settlement_amount = 0.0
                rec.cash_amount = effective

    @api.depends('is_loan_settlement', 'approved_date', 'withdrawal_type')
    def _compute_lock(self):
        config = self.env['zain.configuration'].search([], limit=1)
        lock_cash = config.withdrawal_lock_cash_years if config else 5
        lock_loan = config.withdrawal_lock_loan_years if config else 3

        for rec in self:
            if rec.withdrawal_type != '50' or not rec.approved_date:
                rec.lock_years = 0
                rec.lock_expiry_date = False
                continue
            years = lock_loan if rec.is_loan_settlement else lock_cash
            rec.lock_years = years
            rec.lock_expiry_date = rec.approved_date + relativedelta(years=years)

    # ─────────────────────────────────────────────────────────────────────────
    # Onchange
    # ─────────────────────────────────────────────────────────────────────────

    @api.onchange('member_id')
    def _onchange_member_id(self):
        if not self.member_id:
            return
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

    # ─────────────────────────────────────────────────────────────────────────
    # Constraints
    # ─────────────────────────────────────────────────────────────────────────

    @api.constrains('member_id', 'withdrawal_type', 'requested_amount')
    def _check_eligibility(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_50 = config.withdrawal_50_min_months if config else 60
        min_75 = config.withdrawal_75_min_months if config else 240

        for rec in self:
            months = rec.member_id.contribution_months
            if rec.withdrawal_type == '50' and months < min_50:
                raise ValidationError(_(
                    'Member "%s" has %d contribution months. '
                    'Minimum for 50%% withdrawal: %d months.'
                ) % (rec.member_id.employee_id.name, months, min_50))
            if rec.withdrawal_type == '75' and months < min_75:
                raise ValidationError(_(
                    'Member "%s" has %d contribution months. '
                    'Minimum for 75%% withdrawal: %d months.'
                ) % (rec.member_id.employee_id.name, months, min_75))

    def _check_no_active_request(self):
        for rec in self:
            existing = self.search([
                ('member_id', '=', rec.member_id.id),
                ('state', 'not in', ('approved', 'cancelled')),
                ('id', '!=', rec.id),
            ], limit=1)
            if existing:
                raise UserError(_(
                    'Member "%s" already has an active withdrawal request (%s).'
                ) % (rec.member_id.employee_id.name, existing.name))

    # ─────────────────────────────────────────────────────────────────────────
    # Workflow buttons
    # ─────────────────────────────────────────────────────────────────────────

    def action_submit(self):
        config = self.env['zain.configuration'].search([], limit=1)
        min_50 = config.withdrawal_50_min_months if config else 60
        min_75 = config.withdrawal_75_min_months if config else 240

        for rec in self:
            months = rec.member_id.contribution_months
            if rec.withdrawal_type == '50' and months < min_50:
                raise UserError(_(
                    'Cannot submit: member "%s" has only %d contribution months '
                    '(minimum for 50%% withdrawal: %d).'
                ) % (rec.member_id.employee_id.name, months, min_50))
            if rec.withdrawal_type == '75' and months < min_75:
                raise UserError(_(
                    'Cannot submit: member "%s" has only %d contribution months '
                    '(minimum for 75%% withdrawal: %d).'
                ) % (rec.member_id.employee_id.name, months, min_75))
            if rec.requested_amount <= 0:
                raise UserError(_('Requested amount must be greater than zero.'))
            if rec.requested_amount > rec.eligible_amount:
                raise UserError(_(
                    'Requested amount (%.3f JOD) exceeds eligible amount (%.3f JOD).'
                ) % (rec.requested_amount, rec.eligible_amount))
            rec._check_no_active_request()

        self.write({'state': 'hr_review'})

    def action_to_approval_1(self):
        self.write({'state': 'approval_1'})

    def action_to_approval_2(self):
        self.write({'state': 'approval_2'})

    def action_approve(self):
        for rec in self:
            if rec.cash_amount > 0 and not rec.payment_method:
                raise UserError(_(
                    'Please set a Disbursement Method before approving.'
                ))
            if rec.payment_method == 'cheque' and not rec.cheque_number:
                raise UserError(_('Please enter the Cheque Number before approving.'))

            rec.approved_amount = min(rec.requested_amount, rec.eligible_amount)
            rec.approved_date = fields.Date.today()
            rec.member_balance_at_approval = rec.member_id.current_balance
            rec.state = 'approved'

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
