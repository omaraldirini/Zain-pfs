from odoo import api, fields, models
from dateutil.relativedelta import relativedelta


class ZainMember(models.Model):
    """Central member profile.  One record per employee enrolled in the fund.
    Balances are recomputed dynamically against `as_of_date`.
    """
    _name = 'zain.member'
    _description = 'PFS Member'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'employee_id'

    # ── Employee linkage ────────────────────────────────────────────────────
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        ondelete='restrict', tracking=True,
    )
    employee_number = fields.Char(
        related='employee_id.employee_number', store=True, readonly=True,
    )
    department_id = fields.Many2one(
        related='employee_id.department_id', store=True, readonly=True,
    )
    job_id = fields.Many2one(
        related='employee_id.job_id', store=True, readonly=True,
    )
    join_date = fields.Date(
        related='employee_id.date_start', store=True, readonly=True,
        string='Company Join Date',
    )
    fund_join_date = fields.Date(string='Fund Join Date', required=True, tracking=True)
    active = fields.Boolean(default=True)
    state = fields.Selection([
        ('active', 'Active'),
        ('resigned', 'Resigned'),
        ('terminated', 'Terminated'),
    ], default='active', tracking=True)

    # ── "As of Date" balance inquiry ────────────────────────────────────────
    as_of_date = fields.Date(
        string='As of Date',
        default=fields.Date.today,
        help='All computed balance fields are evaluated as of this date.',
    )

    # ── Contribution totals (populated by payroll integration / manual entry) ─
    employee_contribution = fields.Float(
        string='Employee Contributions', compute='_compute_balances', store=False,
    )
    company_contribution = fields.Float(
        string='Company Contributions', compute='_compute_balances', store=False,
    )
    total_profits = fields.Float(
        string='Total Profits', compute='_compute_balances', store=False,
    )
    total_withdrawn = fields.Float(
        string='Total Withdrawn', compute='_compute_balances', store=False,
    )
    current_balance = fields.Float(
        string='Current Balance', compute='_compute_balances', store=False,
        help='(Employee Contributions + Company Contributions + Profits) – Withdrawals',
    )
    contribution_months = fields.Integer(
        string='Contribution Months', compute='_compute_contribution_months', store=False,
    )

    # ── Loan summary ────────────────────────────────────────────────────────
    loan_ids = fields.One2many('zain.loan', 'member_id', string='Loans')
    land_loan_ids = fields.One2many('zain.land.loan', 'member_id', string='Land Loans')
    active_loan_count = fields.Integer(compute='_compute_loan_summary')
    loan_balance = fields.Float(
        string='Loan Balance', compute='_compute_loan_summary',
        help='Total outstanding balance across all active personal loans.',
    )
    land_loan_balance = fields.Float(
        string='Land Loan Balance', compute='_compute_loan_summary',
    )

    # ── Withdrawal eligibility ───────────────────────────────────────────────
    withdrawal_ids = fields.One2many('zain.withdrawal', 'member_id', string='Withdrawals')
    eligibility_50 = fields.Float(
        string='Eligibility (50%)', compute='_compute_eligibility', store=False,
    )
    eligibility_75 = fields.Float(
        string='Eligibility (75%)', compute='_compute_eligibility', store=False,
    )

    # ── Resignation ─────────────────────────────────────────────────────────
    resignation_ids = fields.One2many('zain.resignation', 'member_id', string='Resignations')

    # ── Helpers ─────────────────────────────────────────────────────────────
    @api.depends('as_of_date', 'fund_join_date')
    def _compute_contribution_months(self):
        today = fields.Date.today()
        for rec in self:
            if rec.fund_join_date:
                end = rec.as_of_date or today
                delta = relativedelta(end, rec.fund_join_date)
                rec.contribution_months = max(0, delta.years * 12 + delta.months)
            else:
                rec.contribution_months = 0

    @api.depends('as_of_date', 'fund_join_date')
    def _compute_balances(self):
        """Stub: replace with real aggregation from contribution lines / journal items."""
        for rec in self:
            rec.employee_contribution = 0.0
            rec.company_contribution = 0.0
            rec.total_profits = 0.0
            rec.total_withdrawn = 0.0
            rec.current_balance = 0.0

    @api.depends('loan_ids.state', 'loan_ids.remaining_balance',
                 'land_loan_ids.remaining_balance')
    def _compute_loan_summary(self):
        for rec in self:
            active_loans = rec.loan_ids.filtered(lambda l: l.state == 'active')
            rec.active_loan_count = len(active_loans)
            rec.loan_balance = sum(active_loans.mapped('remaining_balance'))
            rec.land_loan_balance = sum(
                rec.land_loan_ids.filtered(
                    lambda l: l.state == 'active'
                ).mapped('remaining_balance')
            )

    @api.depends('current_balance', 'withdrawal_ids.state', 'withdrawal_ids.approved_date')
    def _compute_eligibility(self):
        """Calculate 50% and 75% eligibility per BRD §5.5."""
        for rec in self:
            balance = rec.current_balance
            # 50%: total balance / 2 minus locked amounts still within lock window
            locked_50 = rec._get_locked_50_amount()
            rec.eligibility_50 = max(0.0, (balance / 2) - locked_50)
            # 75%: 75% of balance minus all previous 50%/75% withdrawals
            prev_withdrawn = sum(
                rec.withdrawal_ids.filtered(
                    lambda w: w.state == 'approved'
                ).mapped('approved_amount')
            )
            rec.eligibility_75 = max(0.0, (balance * 0.75) - prev_withdrawn)

    def _get_locked_50_amount(self):
        """Return the portion of 50% eligibility still under lock window."""
        self.ensure_one()
        config = self.env['zain.configuration'].search([], limit=1)
        lock_cash_years = config.withdrawal_lock_cash_years if config else 5
        lock_loan_years = config.withdrawal_lock_loan_years if config else 3
        today = fields.Date.today()
        locked = 0.0
        for w in self.withdrawal_ids.filtered(
            lambda w: w.state == 'approved' and w.withdrawal_type == '50'
        ):
            lock_years = lock_loan_years if w.is_loan_settlement else lock_cash_years
            if w.approved_date:
                unlock_date = w.approved_date + relativedelta(years=lock_years)
                if today < unlock_date:
                    locked += w.approved_amount
        return locked
