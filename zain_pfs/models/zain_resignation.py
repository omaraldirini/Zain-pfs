from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


REASON_SELECTION = [
    ('personal', 'Personal'),
    ('fraud', 'Fraud / Termination'),
    ('death', 'Death'),
]


class ZainResignation(models.Model):
    """Resignation & settlement per BRD §5.4.

    Settlement formula:
        Final Settlement = (Employee Contributions)
                         + (Company Contributions × Vesting%)
                         - Withdrawn Amounts
                         + ((Total Profits / 3) + (Total Profits / 3 × 2 × Vesting%))
                         - Outstanding Loan Balances

    Vesting tiers (personal resignation):
        < 36 months  →  0%
        36–47 months → 60%
        48–59 months → 80%
        60+ months   → 100%

    Special cases:
        Death  → 100% vesting, no contribution cut-off
        Fraud  →   0% vesting, only employee contributions returned

    Contribution cut-off:
        If resigned before the 15th of the month, that month's
        contributions are excluded (death is exempt from this rule).
    """
    _name = 'zain.resignation'
    _description = 'PFS Resignation & Settlement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='restrict', tracking=True,
    )
    resignation_date = fields.Date(string='Resignation / Termination Date', required=True)
    reason = fields.Selection(REASON_SELECTION, string='Reason', required=True, tracking=True)
    is_fraudulent = fields.Boolean(
        string='Fraudulent Termination',
        compute='_compute_is_fraudulent', store=True,
    )
    notes = fields.Text()

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    settlement_date = fields.Date(string='Settlement Date', readonly=True)

    # ── Member summary ────────────────────────────────────────────────────────
    member_contribution_months = fields.Integer(
        related='member_id.contribution_months',
        string='Current Contribution Months', readonly=True,
    )
    member_balance = fields.Float(
        related='member_id.current_balance',
        string='Current Balance (JOD)', readonly=True,
    )

    # ── Settlement calculation (store=False: always computed fresh from source data) ─
    cutoff_date = fields.Date(
        string='Contribution Cut-off Date',
        compute='_compute_settlement', store=False,
        help='Contributions up to and including this date are counted.',
    )
    contribution_months_at_resignation = fields.Integer(
        string='Contribution Months at Resignation',
        compute='_compute_settlement', store=False,
    )
    vesting_percent = fields.Float(
        string='Vesting %',
        compute='_compute_settlement', store=False,
    )
    employee_contributions = fields.Float(
        string='Employee Contributions (JOD)',
        compute='_compute_settlement', store=False,
    )
    company_contribution_gross = fields.Float(
        string='Company Contributions – Gross (JOD)',
        compute='_compute_settlement', store=False,
    )
    company_contributions_entitled = fields.Float(
        string='Company Contributions – Vested (JOD)',
        compute='_compute_settlement', store=False,
    )
    total_profits_gross = fields.Float(
        string='Total Profits – Gross (JOD)',
        compute='_compute_settlement', store=False,
    )
    profits_entitled = fields.Float(
        string='Profits – Entitled (JOD)',
        compute='_compute_settlement', store=False,
        help='(Profits / 3) + (Profits / 3 × 2 × Vesting%)',
    )
    withdrawn_amounts = fields.Float(
        string='Previously Withdrawn (JOD)',
        compute='_compute_settlement', store=False,
    )
    outstanding_loans = fields.Float(
        string='Outstanding Loan Balance (JOD)',
        compute='_compute_settlement', store=False,
    )
    final_settlement = fields.Float(
        string='Final Settlement Amount (JOD)',
        compute='_compute_settlement', store=False,
    )

    # ── Frozen values (stored at approval time) ───────────────────────────────
    approved_contribution_months = fields.Integer(
        string='Contribution Months (Approved)', readonly=True,
    )
    approved_vesting_percent = fields.Float(
        string='Vesting % (Approved)', readonly=True,
    )
    approved_final_settlement = fields.Float(
        string='Approved Settlement Amount (JOD)', readonly=True, tracking=True,
    )

    # ── Disbursement ──────────────────────────────────────────────────────────
    payment_method = fields.Selection([
        ('cheque', 'Cheque'),
        ('bank_transfer', 'Bank Transfer'),
    ], string='Payment Method', tracking=True)
    cheque_number = fields.Char(string='Cheque Number')
    cheque_date = fields.Date(string='Cheque Date')
    cheque_amount = fields.Float(string='Cheque Amount (JOD)')

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.resignation') or 'New'
        return super().create(vals_list)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('reason')
    def _compute_is_fraudulent(self):
        for rec in self:
            rec.is_fraudulent = rec.reason == 'fraud'

    @api.depends('member_id', 'resignation_date', 'reason')
    def _compute_settlement(self):
        config = self.env['zain.configuration'].search([], limit=1)
        if config:
            t1m, t1p = config.vesting_tier_1_months, config.vesting_tier_1_percent
            t2m, t2p = config.vesting_tier_2_months, config.vesting_tier_2_percent
            t3m, t3p = config.vesting_tier_3_months, config.vesting_tier_3_percent
        else:
            t1m, t1p = 36, 0.6
            t2m, t2p = 48, 0.8
            t3m, t3p = 60, 1.0

        zero = dict(
            cutoff_date=False,
            contribution_months_at_resignation=0,
            vesting_percent=0.0,
            employee_contributions=0.0,
            company_contribution_gross=0.0,
            company_contributions_entitled=0.0,
            total_profits_gross=0.0,
            profits_entitled=0.0,
            withdrawn_amounts=0.0,
            outstanding_loans=0.0,
            final_settlement=0.0,
        )

        for rec in self:
            if not rec.member_id or not rec.resignation_date:
                for k, v in zero.items():
                    setattr(rec, k, v)
                continue

            res_date = rec.resignation_date
            is_death = rec.reason == 'death'

            # ── Contribution cut-off ─────────────────────────────────────────
            # Death: no cut-off — use full resignation date
            # Others: if resigned before 15th, exclude current month
            if is_death or res_date.day >= 15:
                cutoff = res_date
            else:
                cutoff = res_date - relativedelta(months=1)

            rec.cutoff_date = cutoff

            # ── Count posted contribution months up to cutoff ────────────────
            posted = self.env['zain.member.contribution'].search([
                ('member_id', '=', rec.member_id.id),
                ('state', '=', 'posted'),
                ('date', '<=', cutoff),
            ])
            months = len(posted)
            rec.contribution_months_at_resignation = months

            # ── Vesting % ────────────────────────────────────────────────────
            if is_death:
                vesting = 1.0
            elif rec.reason == 'fraud':
                vesting = 0.0
            else:
                if months >= t3m:
                    vesting = t3p
                elif months >= t2m:
                    vesting = t2p
                elif months >= t1m:
                    vesting = t1p
                else:
                    vesting = 0.0

            rec.vesting_percent = vesting

            # ── Pull financials directly from source records (not via member) ─
            emp_cont = sum(posted.mapped('employee_amount'))
            co_cont = sum(posted.mapped('company_amount'))

            profit_lines = self.env['zain.profit.distribution.line'].search([
                ('member_id', '=', rec.member_id.id),
                ('posted', '=', True),
                ('distribution_id.date', '<=', cutoff),
            ])
            profits = sum(profit_lines.mapped('profit_amount'))

            withdrawals = self.env['zain.withdrawal'].search([
                ('member_id', '=', rec.member_id.id),
                ('state', '=', 'approved'),
                ('approved_date', '<=', cutoff),
            ])
            withdrawn = sum(withdrawals.mapped('approved_amount'))

            active_loans = self.env['zain.loan'].search([
                ('member_id', '=', rec.member_id.id),
                ('state', '=', 'active'),
            ])
            land_loans = self.env['zain.land.loan'].search([
                ('member_id', '=', rec.member_id.id),
                ('state', '=', 'active'),
            ])
            loans = (
                sum(active_loans.mapped('remaining_balance'))
                + sum(land_loans.mapped('remaining_balance'))
            )

            # ── Assign breakdown fields ──────────────────────────────────────
            rec.employee_contributions = emp_cont
            rec.company_contribution_gross = co_cont
            rec.company_contributions_entitled = co_cont * vesting
            rec.total_profits_gross = profits
            rec.profits_entitled = (profits / 3.0) + (profits / 3.0 * 2.0 * vesting)
            rec.withdrawn_amounts = withdrawn
            rec.outstanding_loans = loans

            rec.final_settlement = (
                emp_cont
                + (co_cont * vesting)
                - withdrawn
                + (profits / 3.0) + (profits / 3.0 * 2.0 * vesting)
                - loans
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Workflow buttons
    # ─────────────────────────────────────────────────────────────────────────

    def action_submit(self):
        for rec in self:
            if rec.member_id.state != 'active':
                raise UserError(_(
                    'Member "%s" is not active (current status: %s).'
                ) % (rec.member_id.employee_id.name, rec.member_id.state))
            existing = self.search([
                ('member_id', '=', rec.member_id.id),
                ('state', 'not in', ('approved', 'cancelled')),
                ('id', '!=', rec.id),
            ], limit=1)
            if existing:
                raise UserError(_(
                    'Member "%s" already has an open resignation request (%s).'
                ) % (rec.member_id.employee_id.name, existing.name))
        self.write({'state': 'submitted'})

    def action_approve(self):
        for rec in self:
            if not rec.payment_method:
                raise UserError(_('Please set a Payment Method before approving the settlement.'))
            if rec.payment_method == 'cheque' and not rec.cheque_number:
                raise UserError(_('Please enter the Cheque Number before approving.'))

            # Freeze settlement values at approval time
            rec.approved_contribution_months = rec.contribution_months_at_resignation
            rec.approved_vesting_percent = rec.vesting_percent
            rec.approved_final_settlement = rec.final_settlement
            rec.settlement_date = fields.Date.today()

            # Update member state
            new_state = 'terminated' if rec.reason == 'death' else 'resigned'
            rec.member_id.write({'state': new_state})

            rec.state = 'approved'

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
