from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


REASON_SELECTION = [
    ('personal', 'Personal'),
    ('fraud', 'Fraud / Termination'),
    ('death', 'Death'),
]


class ZainResignation(models.Model):
    """Resignation & settlement per BRD §5.4.
    Settlement = (Emp Contributions)
               + (Company Contributions × Vesting%)
               - Withdrawn Amounts
               + ((Total Profits / 3) + (Total Profits / 3 × 2 × Vesting%))
               - Outstanding Loan Balances
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
    resignation_date = fields.Date(string='Resignation Date', required=True)
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

    # ── Settlement calculation fields (read-only, computed) ───────────────────
    contribution_months_at_resignation = fields.Integer(
        string='Contribution Months at Resignation',
        compute='_compute_settlement', store=True,
    )
    vesting_percent = fields.Float(
        string='Vesting %', compute='_compute_settlement', store=True,
    )
    employee_contributions = fields.Float(
        string='Employee Contributions', compute='_compute_settlement', store=True,
    )
    company_contributions_entitled = fields.Float(
        string='Company Contributions (Vested)', compute='_compute_settlement', store=True,
    )
    profits_entitled = fields.Float(
        string='Profits (Vested)', compute='_compute_settlement', store=True,
    )
    withdrawn_amounts = fields.Float(
        string='Previously Withdrawn', compute='_compute_settlement', store=True,
    )
    outstanding_loans = fields.Float(
        string='Outstanding Loan Balance', compute='_compute_settlement', store=True,
    )
    final_settlement = fields.Float(
        string='Final Settlement Amount', compute='_compute_settlement', store=True,
    )

    # ── Sequence ──────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('zain.resignation') or 'New'
        return super().create(vals_list)

    # ── Computed ──────────────────────────────────────────────────────────────
    @api.depends('reason')
    def _compute_is_fraudulent(self):
        for rec in self:
            rec.is_fraudulent = rec.reason == 'fraud'

    @api.depends('member_id', 'resignation_date', 'reason')
    def _compute_settlement(self):
        config = self.env['zain.configuration'].search([], limit=1)
        for rec in self:
            if not rec.member_id or not rec.resignation_date:
                rec.contribution_months_at_resignation = 0
                rec.vesting_percent = 0.0
                rec.employee_contributions = 0.0
                rec.company_contributions_entitled = 0.0
                rec.profits_entitled = 0.0
                rec.withdrawn_amounts = 0.0
                rec.outstanding_loans = 0.0
                rec.final_settlement = 0.0
                continue

            # Contribution cut-off: if resigned before 15th, exclude that month
            res_date = rec.resignation_date
            if res_date.day < 15:
                from dateutil.relativedelta import relativedelta
                cutoff = res_date - relativedelta(months=1)
            else:
                cutoff = res_date

            from dateutil.relativedelta import relativedelta as rd
            delta = rd(cutoff, rec.member_id.fund_join_date)
            months = max(0, delta.years * 12 + delta.months)
            rec.contribution_months_at_resignation = months

            # Vesting % based on config tiers
            if rec.reason == 'death':
                vesting = 1.0
            elif rec.reason == 'fraud':
                vesting = 0.0
            else:
                if config:
                    t3m = config.vesting_tier_3_months
                    t3p = config.vesting_tier_3_percent
                    t2m = config.vesting_tier_2_months
                    t2p = config.vesting_tier_2_percent
                    t1m = config.vesting_tier_1_months
                    t1p = config.vesting_tier_1_percent
                else:
                    t3m, t3p = 60, 1.0
                    t2m, t2p = 48, 0.8
                    t1m, t1p = 36, 0.6
                if months >= t3m:
                    vesting = t3p
                elif months >= t2m:
                    vesting = t2p
                elif months >= t1m:
                    vesting = t1p
                else:
                    vesting = 0.0

            rec.vesting_percent = vesting

            # Pull raw values from member (stub: real values come from contributions)
            emp_cont = rec.member_id.employee_contribution
            co_cont = rec.member_id.company_contribution
            profits = rec.member_id.total_profits
            withdrawn = rec.member_id.total_withdrawn
            loans = rec.member_id.loan_balance + rec.member_id.land_loan_balance

            rec.employee_contributions = emp_cont
            rec.company_contributions_entitled = co_cont * vesting
            # Profit formula: (P/3) + (P/3 * 2 * vesting%)
            rec.profits_entitled = (profits / 3) + (profits / 3 * 2 * vesting)
            rec.withdrawn_amounts = withdrawn
            rec.outstanding_loans = loans

            rec.final_settlement = (
                emp_cont
                + (co_cont * vesting)
                - withdrawn
                + (profits / 3) + (profits / 3 * 2 * vesting)
                - loans
            )

    # ── Workflow ──────────────────────────────────────────────────────────────
    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        for rec in self:
            rec.member_id.write({'state': 'resigned'})
            rec.state = 'approved'

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
