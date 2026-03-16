from calendar import monthrange
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ZainProfitDistribution(models.Model):
    """Annual profit distribution to fund members per BRD §5.6.

    Calculation flow:
        1. Admin sets fiscal_year_start / fiscal_year_end and total_profit.
        2. 'Generate Member Lines' creates one line per active member and
           auto-computes each member's avg_monthly_balance from posted
           contribution records within the fiscal year.
        3. Admin may adjust avg_monthly_balance manually before submitting.
        4. Lines are submitted for Fund Committee approval.
        5. On approval, profit_amount is frozen per line.
        6. On posting, all lines are marked posted; member profit totals
           update automatically via zain.member._compute_balances.

    Average Member Balance = Sum(month-end balances over 12 months) / 12
    Month-end balance      = Contributions + Prior Profits − Withdrawals
    Member Share %         = Avg Member Balance / Total Avg Balance
    Member Profit          = Total Approved Profit × Member Share %
    """
    _name = 'zain.profit.distribution'
    _description = 'PFS Profit Distribution'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    fiscal_year = fields.Char(string='Fiscal Year', required=True, help='e.g. 2025')
    fiscal_year_start = fields.Date(
        string='Fiscal Year Start', required=True,
        help='First day of the fiscal year (e.g. 2025-01-01).',
    )
    fiscal_year_end = fields.Date(
        string='Fiscal Year End', required=True,
        help='Last day of the fiscal year (e.g. 2025-12-31).',
    )
    date = fields.Date(
        string='Distribution Date', default=fields.Date.today,
        help='Used as the distribution date when computing member profit totals.',
    )
    total_profit = fields.Float(
        string='Total Profit to Distribute (JOD)', required=True, tracking=True,
    )

    # ── Computed summary fields ───────────────────────────────────────────────
    total_avg_balance = fields.Float(
        string='Total Avg Balance – All Members (JOD)',
        compute='_compute_totals', store=True,
    )
    total_profit_distributed = fields.Float(
        string='Total Profit Distributed (JOD)',
        compute='_compute_totals', store=True,
        help='Sum of all member profit amounts. Should equal Total Profit.',
    )
    distribution_diff = fields.Float(
        string='Rounding Difference (JOD)',
        compute='_compute_totals', store=True,
        help='total_profit − total_profit_distributed. Should be near zero.',
    )
    line_count = fields.Integer(
        string='Member Count', compute='_compute_totals', store=True,
    )

    # ── Approval / posting metadata ───────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_approval', 'Pending Committee Approval'),
        ('approved', 'Approved'),
        ('posted', 'Posted'),
    ], default='draft', string='Status', tracking=True)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True)
    approval_date = fields.Date(string='Approval Date', readonly=True)
    posting_date = fields.Date(string='Posting Date', readonly=True)
    notes = fields.Text()

    line_ids = fields.One2many(
        'zain.profit.distribution.line', 'distribution_id', string='Member Lines',
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('zain.profit.distribution') or 'New'
                )
        return super().create(vals_list)

    # ─────────────────────────────────────────────────────────────────────────
    # Onchange helpers
    # ─────────────────────────────────────────────────────────────────────────

    @api.onchange('fiscal_year')
    def _onchange_fiscal_year(self):
        """Auto-fill start/end dates when a 4-digit year is entered."""
        if self.fiscal_year and self.fiscal_year.isdigit() and len(self.fiscal_year) == 4:
            year = int(self.fiscal_year)
            self.fiscal_year_start = fields.Date.from_string('%d-01-01' % year)
            self.fiscal_year_end = fields.Date.from_string('%d-12-31' % year)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends(
        'line_ids.avg_monthly_balance',
        'line_ids.profit_amount',
        'total_profit',
    )
    def _compute_totals(self):
        for rec in self:
            rec.total_avg_balance = sum(rec.line_ids.mapped('avg_monthly_balance'))
            distributed = sum(rec.line_ids.mapped('profit_amount'))
            rec.total_profit_distributed = distributed
            rec.distribution_diff = rec.total_profit - distributed
            rec.line_count = len(rec.line_ids)

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_generate_lines(self):
        """Create one line per active member and auto-compute avg_monthly_balance."""
        for rec in self:
            if not rec.fiscal_year_start or not rec.fiscal_year_end:
                raise UserError(_(
                    'Please set Fiscal Year Start and End dates before generating lines.'
                ))
            rec.line_ids.unlink()
            members = self.env['zain.member'].search([('state', '=', 'active')])
            lines = self.env['zain.profit.distribution.line'].create([
                {'distribution_id': rec.id, 'member_id': m.id}
                for m in members
            ])
            lines._compute_avg_monthly_balance()

    def action_submit_for_approval(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_(
                    'No member lines found. '
                    'Use "Generate Member Lines" before submitting.'
                ))
            if not rec.fiscal_year_start or not rec.fiscal_year_end:
                raise UserError(_('Fiscal Year Start and End dates are required.'))
            if rec.total_profit <= 0:
                raise UserError(_('Total Profit must be greater than zero.'))
        self.write({'state': 'pending_approval'})

    def action_approve(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_('Cannot approve a distribution with no member lines.'))
            rec.approved_by = self.env.user
            rec.approval_date = fields.Date.today()
            rec.state = 'approved'

    def action_post(self):
        """Mark all lines as posted. Member profit totals update automatically
        via zain.member._compute_balances (reads posted distribution lines)."""
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_('Distribution must be approved before posting.'))
            if not rec.date:
                raise UserError(_('Please set a Distribution Date before posting.'))
            rec.line_ids.write({'posted': True})
            rec.posting_date = fields.Date.today()
            rec.state = 'posted'

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'posted':
                raise UserError(_(
                    'A posted distribution cannot be reset to draft. '
                    'Contact the system administrator.'
                ))
            rec.line_ids.write({'posted': False})
        self.write({'state': 'draft', 'approved_by': False,
                    'approval_date': False, 'posting_date': False})


class ZainProfitDistributionLine(models.Model):
    _name = 'zain.profit.distribution.line'
    _description = 'Profit Distribution Line'
    _order = 'member_id'

    distribution_id = fields.Many2one(
        'zain.profit.distribution', required=True, ondelete='cascade', index=True,
    )
    member_id = fields.Many2one(
        'zain.member', string='Member', required=True, index=True,
    )
    employee_id = fields.Many2one(
        related='member_id.employee_id', string='Employee', readonly=True,
    )
    department_id = fields.Many2one(
        related='member_id.department_id', string='Department', readonly=True,
    )

    avg_monthly_balance = fields.Float(
        string='Avg Monthly Balance (JOD)',
        help='Auto-computed on line generation; editable before approval.',
    )
    share_percent = fields.Float(
        string='Share %',
        compute='_compute_share', store=True, digits=(16, 6),
    )
    profit_amount = fields.Float(
        string='Profit Amount (JOD)',
        compute='_compute_share', store=True,
    )
    posted = fields.Boolean(string='Posted', default=False, readonly=True)

    # ── Share computation ─────────────────────────────────────────────────────

    @api.depends(
        'avg_monthly_balance',
        'distribution_id.total_avg_balance',
        'distribution_id.total_profit',
    )
    def _compute_share(self):
        for line in self:
            total_avg = line.distribution_id.total_avg_balance
            if total_avg:
                line.share_percent = (line.avg_monthly_balance / total_avg) * 100
                line.profit_amount = (
                    line.avg_monthly_balance / total_avg
                ) * line.distribution_id.total_profit
            else:
                line.share_percent = 0.0
                line.profit_amount = 0.0

    # ── Average monthly balance computation ───────────────────────────────────

    def _compute_avg_monthly_balance(self):
        """Compute avg_monthly_balance for each line from posted contribution
        records within the fiscal year.

        For each calendar month in [fiscal_year_start, fiscal_year_end]:
            month_end_balance = sum(posted employee_amount + company_amount up to month-end)
                              + sum(prior posted distribution profit_amounts up to month-end)
                              - sum(approved withdrawal amounts up to month-end)
        avg = sum(month_end_balances) / number_of_months
        """
        for line in self:
            dist = line.distribution_id
            if not dist.fiscal_year_start or not dist.fiscal_year_end:
                line.avg_monthly_balance = 0.0
                continue

            month_balances = []
            current_month_start = dist.fiscal_year_start.replace(day=1)

            while current_month_start <= dist.fiscal_year_end:
                # Last day of this month
                last_day = monthrange(current_month_start.year, current_month_start.month)[1]
                month_end = current_month_start.replace(day=last_day)
                # Cap at fiscal year end
                if month_end > dist.fiscal_year_end:
                    month_end = dist.fiscal_year_end

                # Contributions (employee + company) up to month_end
                contributions = self.env['zain.member.contribution'].search([
                    ('member_id', '=', line.member_id.id),
                    ('state', '=', 'posted'),
                    ('date', '<=', month_end),
                ])
                emp_cont = sum(contributions.mapped('employee_amount'))
                co_cont = sum(contributions.mapped('company_amount'))

                # Prior posted profit distributions up to month_end
                # (exclude the current distribution being computed)
                prior_profits = self.env['zain.profit.distribution.line'].search([
                    ('member_id', '=', line.member_id.id),
                    ('posted', '=', True),
                    ('distribution_id', '!=', dist.id),
                    ('distribution_id.date', '<=', month_end),
                ])
                profits = sum(prior_profits.mapped('profit_amount'))

                # Approved withdrawals up to month_end
                withdrawals = self.env['zain.withdrawal'].search([
                    ('member_id', '=', line.member_id.id),
                    ('state', '=', 'approved'),
                    ('approved_date', '<=', month_end),
                ])
                withdrawn = sum(withdrawals.mapped('approved_amount'))

                month_balance = emp_cont + co_cont + profits - withdrawn
                month_balances.append(month_balance)

                current_month_start += relativedelta(months=1)

            if month_balances:
                line.avg_monthly_balance = sum(month_balances) / len(month_balances)
            else:
                line.avg_monthly_balance = 0.0
