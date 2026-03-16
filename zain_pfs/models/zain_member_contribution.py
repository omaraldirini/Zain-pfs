from odoo import api, fields, models


class ZainMemberContribution(models.Model):
    """Monthly contribution record for a fund member.
    Each posted line represents one month of contributions
    (employee + company).  Balance computations on zain.member
    aggregate from these lines filtered by as_of_date.
    """
    _name = 'zain.member.contribution'
    _description = 'PFS Monthly Contribution'
    _order = 'date desc'
    _rec_name = 'date'

    member_id = fields.Many2one(
        'zain.member', string='Member', required=True,
        ondelete='cascade', index=True,
    )
    date = fields.Date(
        string='Contribution Month', required=True,
        help='Use the first day of the contribution month (e.g. 2025-01-01 for January 2025).',
    )
    employee_amount = fields.Float(string='Employee Amount (JOD)', required=True, default=0.0)
    company_amount = fields.Float(string='Company Amount (JOD)', required=True, default=0.0)
    total = fields.Float(string='Total', compute='_compute_total', store=True)
    notes = fields.Char(string='Notes')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
    ], string='Status', default='draft', required=True)

    @api.depends('employee_amount', 'company_amount')
    def _compute_total(self):
        for rec in self:
            rec.total = rec.employee_amount + rec.company_amount

    def action_post(self):
        self.write({'state': 'posted'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})
