from odoo import api, fields, models
from dateutil.relativedelta import relativedelta


class ZainMember(models.Model):
    """Central member profile.  One record per employee enrolled in the fund.
    All balance fields are computed dynamically against `as_of_date`.
    """
    _name = 'zain.member'
    _description = 'PFS Member'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'employee_id'

    # ── Employee linkage ─────────────────────────────────────────────────────
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        ondelete='restrict', tracking=True,
    )
    employee_number = fields.Char(string='Employee Number', tracking=True)
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
    ], default='active', string='Status', tracking=True)

    # ── "As of Date" balance inquiry ─────────────────────────────────────────
    as_of_date = fields.Date(
        string='As of Date',
        default=fields.Date.today,
        help='All computed balance fields are evaluated as of this date.',
    )

    # ── Contribution lines ───────────────────────────────────────────────────
    contribution_ids = fields.One2many(
        'zain.member.contribution', 'member_id', string='Contributions',
    )
    contribution_count = fields.Integer(
        string='Posted Contributions',
        compute='_compute_contribution_months',
        store=False,
        help='Number of posted monthly contribution records up to As of Date.',
    )
    contribution_months = fields.Integer(
        string='Contribution Months',
        compute='_compute_contribution_months',
        store=False,
        help='Count of posted contribution lines up to As of Date. '
             'Used for loan/withdrawal eligibility checks.',
    )

    # ── Balance totals (aggregated from contribution lines + profit lines) ────
    employee_contribution = fields.Float(
        string='Employee Contributions',
        compute='_compute_balances',
        store=False,
    )
    company_contribution = fields.Float(
        string='Company Contributions',
        compute='_compute_balances',
        store=False,
    )
    total_profits = fields.Float(
        string='Total Profits',
        compute='_compute_balances',
        store=False,
    )
    total_withdrawn = fields.Float(
        string='Total Withdrawn',
        compute='_compute_balances',
        store=False,
    )
    current_balance = fields.Float(
        string='Current Balance',
        compute='_compute_balances',
        store=False,
        help='(Employee Contributions + Company Contributions + Profits) – Withdrawals',
    )

    # ── Profit distribution lines ────────────────────────────────────────────
    profit_distribution_line_ids = fields.One2many(
        'zain.profit.distribution.line', 'member_id', string='Profit Lines',
    )

    # ── Loan summary ─────────────────────────────────────────────────────────
    loan_ids = fields.One2many('zain.loan', 'member_id', string='Loans')
    land_loan_ids = fields.One2many('zain.land.loan', 'member_id', string='Land Loans')
    active_loan_count = fields.Integer(compute='_compute_loan_summary', store=False)
    loan_balance = fields.Float(
        string='Loan Balance',
        compute='_compute_loan_summary',
        store=False,
        help='Total outstanding balance across all active personal loans.',
    )
    land_loan_balance = fields.Float(
        string='Land Loan Balance',
        compute='_compute_loan_summary',
        store=False,
    )

    # ── Withdrawal eligibility ────────────────────────────────────────────────
    withdrawal_ids = fields.One2many('zain.withdrawal', 'member_id', string='Withdrawals')
    eligibility_50 = fields.Float(
        string='Eligibility (50%)',
        compute='_compute_eligibility',
        store=False,
    )
    eligibility_75 = fields.Float(
        string='Eligibility (75%)',
        compute='_compute_eligibility',
        store=False,
    )

    # ── Resignation ───────────────────────────────────────────────────────────
    resignation_ids = fields.One2many('zain.resignation', 'member_id', string='Resignations')

    # ─────────────────────────────────────────────────────────────────────────
    # Computed methods
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('contribution_ids.state', 'contribution_ids.date', 'as_of_date')
    def _compute_contribution_months(self):
        today = fields.Date.today()
        for rec in self:
            as_of = rec.as_of_date or today
            posted = rec.contribution_ids.filtered(
                lambda c: c.state == 'posted' and c.date <= as_of
            )
            count = len(posted)
            rec.contribution_months = count
            rec.contribution_count = count

    @api.depends(
        'contribution_ids.employee_amount',
        'contribution_ids.company_amount',
        'contribution_ids.state',
        'contribution_ids.date',
        'profit_distribution_line_ids.profit_amount',
        'profit_distribution_line_ids.posted',
        'profit_distribution_line_ids.distribution_id.date',
        'withdrawal_ids.state',
        'withdrawal_ids.approved_amount',
        'withdrawal_ids.approved_date',
        'as_of_date',
    )
    def _compute_balances(self):
        today = fields.Date.today()
        for rec in self:
            as_of = rec.as_of_date or today

            posted_cont = rec.contribution_ids.filtered(
                lambda c: c.state == 'posted' and c.date <= as_of
            )
            emp_cont = sum(posted_cont.mapped('employee_amount'))
            co_cont = sum(posted_cont.mapped('company_amount'))

            profits = sum(
                line.profit_amount
                for line in rec.profit_distribution_line_ids
                if line.posted and line.distribution_id.date
                and line.distribution_id.date <= as_of
            )

            withdrawn = sum(
                w.approved_amount
                for w in rec.withdrawal_ids
                if w.state == 'approved' and w.approved_date
                and w.approved_date <= as_of
            )

            rec.employee_contribution = emp_cont
            rec.company_contribution = co_cont
            rec.total_profits = profits
            rec.total_withdrawn = withdrawn
            rec.current_balance = emp_cont + co_cont + profits - withdrawn

    @api.depends(
        'loan_ids.state',
        'loan_ids.remaining_balance',
        'land_loan_ids.state',
        'land_loan_ids.remaining_balance',
    )
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

    @api.depends(
        'current_balance',
        'withdrawal_ids.state',
        'withdrawal_ids.approved_amount',
        'withdrawal_ids.approved_date',
        'withdrawal_ids.is_loan_settlement',
    )
    def _compute_eligibility(self):
        for rec in self:
            balance = rec.current_balance
            locked_50 = rec._get_locked_50_amount()
            rec.eligibility_50 = max(0.0, (balance / 2.0) - locked_50)

            prev_withdrawn = sum(
                w.approved_amount
                for w in rec.withdrawal_ids
                if w.state == 'approved'
            )
            rec.eligibility_75 = max(0.0, (balance * 0.75) - prev_withdrawn)

    def _get_locked_50_amount(self):
        """Return the portion of 50% eligibility still inside the lock window."""
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
