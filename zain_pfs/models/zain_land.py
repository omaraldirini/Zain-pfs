from odoo import fields, models


class ZainLand(models.Model):
    """Land plot master data managed by the Fund Administrator."""
    _name = 'zain.land'
    _description = 'PFS Land Plot'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Plot Name / Number', required=True)
    area = fields.Float(string='Area (sq m)')
    basin = fields.Char(string='Basin (Hod)')
    plot_number = fields.Char(string='Official Plot Number')
    price = fields.Float(string='Price (JOD)', required=True)
    location = fields.Char(string='Location')
    status = fields.Selection([
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('sold', 'Sold'),
    ], default='available', string='Status', tracking=True)
    reserved_member_id = fields.Many2one(
        'zain.member', string='Reserved By', readonly=True,
    )
    notes = fields.Text()

    def action_mark_available(self):
        self.write({'status': 'available', 'reserved_member_id': False})

    def action_mark_sold(self):
        self.write({'status': 'sold'})
