from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ZainProfitDistribution(models.Model):
    """Annual profit distribution to fund members per BRD §5.6.
    Calculation:
        Average Member Balance = Sum(month-end balances) / 12
        Member Share %         = Avg Member Balance / Total Avg Balance
        Member Profit          = Total Approved Profit × Member Share %
    """
    _name = 'zain.profit.distribution'
    _description = 'PFS Profit Distribution'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', readonly=True, default='New', copy=False)
    fiscal_year = fields.Char(string='Fiscal Year', required=True)
    date = fields.Date(string='Distribution Date', default=fields.Date.today)
    total_profit = fields.Float(string='Total Profit to Distribute (JOD)', required=True)
    total_avg_balance = fields.Float(
        string='Total Avg Balance (All Members)',
        compute='_compute_totals', store=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_approval', 'Pending Committee Approval'),
        ('approved', 'Approved'),
        ('posted', 'Posted to Accounts'),
    ], default='draft', tracking=True)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True)
    notes = fields.Text()

    line_ids = fields.One2many(
        'zain.profit.distribution.line', 'distribution_id', string='Member Lines',
    )

    # ── Sequence ──────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('zain.profit.distribution') or 'New'
                )
        return super().create(vals_list)

    # ── Computed ──────────────────────────────────────────────────────────────
    @api.depends('line_ids.avg_monthly_balance')
    def _compute_totals(self):
        for rec in self:
            rec.total_avg_balance = sum(rec.line_ids.mapped('avg_monthly_balance'))

    # ── Actions ───────────────────────────────────────────────────────────────
    def action_generate_lines(self):
        """Generate one line per active fund member."""
        for rec in self:
            rec.line_ids.unlink()
            members = self.env['zain.member'].search([('state', '=', 'active')])
            lines = [{'distribution_id': rec.id, 'member_id': m.id} for m in members]
            self.env['zain.profit.distribution.line'].create(lines)

    def action_submit_for_approval(self):
        self.write({'state': 'pending_approval'})

    def action_approve(self):
        for rec in self:
            rec.approved_by = self.env.user
            rec.state = 'approved'

    def action_post(self):
        """Post profit amounts to each member's total_profits (stub: create journal entries)."""
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_('Distribution must be approved before posting.'))
            for line in rec.line_ids:
                # TODO: create account.move entries (maker-checker per §5.7.1)
                line.posted = True
            rec.state = 'posted'

    def action_reset_draft(self):
        self.write({'state': 'draft'})


class ZainProfitDistributionLine(models.Model):
    _name = 'zain.profit.distribution.line'
    _description = 'Profit Distribution Line'

    distribution_id = fields.Many2one(
        'zain.profit.distribution', required=True, ondelete='cascade',
    )
    member_id = fields.Many2one('zain.member', string='Member', required=True)
    avg_monthly_balance = fields.Float(
        string='Avg Monthly Balance',
        help='Sum of month-end balances for the fiscal year / 12. Must be entered or computed from contribution history.',
    )
    share_percent = fields.Float(
        string='Share %', compute='_compute_share', store=True,
    )
    profit_amount = fields.Float(
        string='Profit Amount (JOD)', compute='_compute_share', store=True,
    )
    posted = fields.Boolean(string='Posted', default=False, readonly=True)

    @api.depends('avg_monthly_balance', 'distribution_id.total_avg_balance',
                 'distribution_id.total_profit')
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
