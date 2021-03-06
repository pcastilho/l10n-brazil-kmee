# -*- coding: utf-8 -*-
# Copyright (C) 2009  Renato Lima - Akretion, Gabriel C. Stabel
# Copyright (C) 2012  Raphaël Valyi - Akretion
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

from openerp import models, fields, api, _
from openerp.exceptions import except_orm
from openerp.addons import decimal_precision as dp
from openerp.addons.l10n_br_base.tools.misc import calc_price_ratio


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.one
    @api.depends('order_line.price_unit', 'order_line.product_qty',
                 'order_line.taxes_id', 'order_line.freight_value',
                 'order_line.other_costs_value', 'order_line.insurance_value',
                 )
    def _compute_amount(self):
        amount_untaxed = 0.0
        amount_tax = 0.0
        amount_freight = 0.0
        amount_costs = 0.0
        amount_insurance = 0.0

        for line in self.order_line:
            price = line._calc_line_base_price(line)
            qty = line._calc_line_quantity(line)
            taxes = line.taxes_id.compute_all(
                price, qty,
                product=line.product_id,
                partner=self.partner_id,
                fiscal_position=self.fiscal_position,
                insurance_value=self.amount_insurance,
                freight_value=self.amount_freight,
                other_costs_value=self.amount_costs,
            )

            amount_untaxed += line.price_subtotal
            amount_freight += line.freight_value
            amount_costs += line.other_costs_value
            amount_insurance += line.insurance_value

            for tax in taxes['taxes']:
                tax_brw = self.env['account.tax'].browse(tax['id'])
                if not tax_brw.tax_code_id.tax_discount:
                    amount_tax += tax.get('amount', 0.0)

        self.amount_untaxed = self.pricelist_id.currency_id.round(
            amount_untaxed)
        self.amount_tax = self.pricelist_id.currency_id.round(amount_tax)
        self.amount_total = self.pricelist_id.currency_id.round(
            amount_untaxed + amount_tax)
        self.amount_total += (amount_freight + amount_costs + amount_insurance)

    @api.model
    @api.returns('l10n_br_account.fiscal_category')
    def _default_fiscal_category(self):
        company = self.env['res.company'].browse(self.env.user.company_id.id)
        return company.purchase_fiscal_category_id

    fiscal_category_id = fields.Many2one(
        'l10n_br_account.fiscal.category', 'Categoria Fiscal',
        default=_default_fiscal_category,
        domain="""[('type', '=', 'input'), ('state', '=', 'approved'),
            ('journal_type', '=', 'purchase')]""")
    amount_untaxed = fields.Float(
        compute='_compute_amount', digits=dp.get_precision('Purchase Price'),
        string='Untaxed Amount', store=True, help="The amount without tax")
    amount_tax = fields.Float(
        compute='_compute_amount', digits=dp.get_precision('Purchase Price'),
        string='Taxes', store=True, help="The tax amount")
    amount_total = fields.Float(
        compute='_compute_amount', digits=dp.get_precision('Purchase Price'),
        string='Total', store=True, help="The total amount")
    cnpj_cpf = fields.Char(
        string=u'CNPJ/CPF',
        related='partner_id.cnpj_cpf',
    )
    legal_name = fields.Char(
        string=u'Razão Social',
        related='partner_id.legal_name',
    )
    ie = fields.Char(
        string=u'Inscrição Estadual',
        related='partner_id.inscr_est',
    )

    @api.one
    def _set_amount_freight(self):
        for line in self.order_line:
            line.write({
                'freight_value': calc_price_ratio(
                    line.price_subtotal,
                    self.amount_freight,
                    self.amount_untaxed),
                })

    @api.one
    def _set_amount_costs(self):
        for line in self.order_line:
            line.write({
                'other_costs_value': calc_price_ratio(
                    line.price_subtotal,
                    self.amount_costs,
                    self.amount_untaxed),
                })

    @api.one
    def _set_amount_insurance(self):
        for line in self.order_line:
            line.write({
                'insurance_value': calc_price_ratio(
                    line.price_subtotal,
                    self.amount_insurance,
                    self.amount_untaxed),
                })

    @api.one
    def _get_costs_value(self):
        freight = costs = insurance = 0
        for line in self.order_line:
            freight += line.freight_value
            costs += line.other_costs_value
            insurance += line.insurance_value

        self.amount_freight = freight
        self.amount_costs = costs
        self.amount_insurance = insurance

    amount_freight = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_freight,
        string='Frete', default=0.00, digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})
    amount_costs = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_costs,
        string='Outros Custos', default=0.00,
        digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})
    amount_insurance = fields.Float(
        compute=_get_costs_value, inverse=_set_amount_insurance,
        string='Seguro', default=0.00, digits=dp.get_precision('Account'),
        readonly=True, states={'draft': [('readonly', False)]})

    @api.model
    def _fiscal_position_map(self, result, **kwargs):
        """Método para chamar a definição de regras fiscais"""
        ctx = dict(self._context)
        kwargs['fiscal_category_id'] = ctx.get('fiscal_category_id')

        ctx.update({'use_domain': ('use_purchase', '=', True)})
        return self.env[
            'account.fiscal.position.rule'].with_context(
                ctx).apply_fiscal_mapping(result, **kwargs)

    @api.onchange('fiscal_category_id', 'fiscal_position')
    def onchange_fiscal(self):

        result = {'value': {'fiscal_position': False}}

        if self.partner_id and self.company_id:
            kwargs = {
                'company_id': self.company_id.id,
                'partner_id': self.partner_id.id,
                'partner_invoice_id': self.partner_id.id,
                'fiscal_category_id': self.fiscal_category_id.id,
                'partner_shipping_id': self.dest_address_id.id,
            }
            result = self._fiscal_position_map(result, **kwargs)

        self.fiscal_position = result['value'].get('fiscal_position')

    @api.multi
    def action_invoice_create(self):
        context = dict(self.env.context)
        context.update({'fiscal_document_code': 55})
        return super(PurchaseOrder,
                     self.with_context(context)).action_invoice_create()

    # TODO migrate to new API
    def _prepare_inv_line(self, cr, uid, account_id, order_line, context=None):

        result = super(PurchaseOrder, self)._prepare_inv_line(
            cr, uid, account_id, order_line, context)

        order = order_line.order_id

        result['fiscal_category_id'] = order_line.fiscal_category_id and \
            order_line.fiscal_category_id.id or \
            order.fiscal_category_id and \
            order.fiscal_category_id.id

        result['fiscal_position'] = order_line.fiscal_position and \
            order_line.fiscal_position.id or \
            order.fiscal_position and \
            order.fiscal_position.id

        result['cfop_id'] = order.fiscal_position and \
            order.fiscal_position.cfop_id and \
            order.fiscal_position.cfop_id.id or \
            order.fiscal_position and \
            order.fiscal_position.cfop_id and \
            order.fiscal_position.cfop_id.id

        result['partner_id'] = order_line.partner_id.id
        result['company_id'] = order_line.company_id.id

        result['insurance_value'] = order_line.insurance_value
        result['other_costs_value'] = order_line.other_costs_value
        result['freight_value'] = order_line.freight_value
        return result

    @api.model
    def _prepare_invoice(self, order, line_ids):
        result = super(PurchaseOrder, self)._prepare_invoice(order, line_ids)

        company_id = order.company_id
        if not company_id.document_serie_product_ids:
            raise except_orm(
                _('No fiscal document serie found!'),
                _("No fiscal document serie found for selected \
                company %s") % (order.company_id.name))

        journal_id = order.fiscal_category_id.property_journal.id
        if not journal_id:
            raise except_orm(
                _(u'Nenhuma Diário!'),
                _(u"Categoria de operação fisca: '%s', não tem um \
                diário contábil para a empresa %s") % (
                    order.fiscal_category_id.name,
                    order.company_id.name))

        comment = []
        if order.fiscal_position.inv_copy_note and order.fiscal_position.note:
            comment.append(order.fiscal_position.note)
        if order.notes:
            comment.append(order.notes)

        result['issuer'] = '1'
        # COPIAR OBS FISCAIS TBM???
        result['comment'] = ' - '.join(comment)
        result['journal_id'] = journal_id
        result['fiscal_category_id'] = order.fiscal_category_id.id
        result['fiscal_position'] = order.fiscal_position.id

        return result

    # TODO migrate to new API
    def _prepare_order_line_move(self, cr, uid, order, order_line, picking_id,
                                 group_id, context=None):
        result = super(PurchaseOrder, self)._prepare_order_line_move(
            cr, uid, order, order_line, picking_id, group_id, context)
        for move in result:
            move['fiscal_category_id'] = order_line.fiscal_category_id.id
            move['fiscal_position'] = order_line.fiscal_position.id
        return result

    @api.model
    def _create_stock_moves(self, order, order_lines, picking_id=None):
        result = super(PurchaseOrder, self)._create_stock_moves(
            order, order_lines, picking_id)
        if picking_id:
            picking_values = {
                'fiscal_category_id': order.fiscal_category_id.id,
                'fiscal_position': order.fiscal_position.id,
            }
            picking = self.env['stock.picking'].browse(picking_id)
            picking.write(picking_values)
        return result


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    fiscal_category_id = fields.Many2one(
        'l10n_br_account.fiscal.category', 'Categoria Fiscal',
        domain="[('type', '=', 'input'), ('journal_type', '=', 'purchase')]")
    fiscal_position = fields.Many2one(
        'account.fiscal.position', u'Posição Fiscal',
        domain="[('fiscal_category_id', '=', fiscal_category_id)]")
    freight_value = fields.Float(
        string='Freight',
        default=0.0,
        digits_compute=dp.get_precision('Account'))
    other_costs_value = fields.Float(
        string='Other costs',
        default=0.0,
        digits_compute=dp.get_precision('Account'))
    insurance_value = fields.Float(
        'Insurance',
        default=0.0,
        digits=dp.get_precision('Account'))

    @api.model
    def _fiscal_position_map(self, result, **kwargs):
        context = dict(self.env.context)
        context.update({'use_domain': ('use_purchase', '=', True)})
        fp_rule_obj = self.env['account.fiscal.position.rule']

        partner_invoice = self.env['res.partner'].browse(
            kwargs.get('partner_invoice_id'))

        product_fc_id = fp_rule_obj.with_context(
            context).product_fiscal_category_map(
            kwargs.get('product_id'),
            kwargs.get('fiscal_category_id'),
            partner_invoice.state_id.id)

        if product_fc_id:
            kwargs['fiscal_category_id'] = product_fc_id

        result['value']['fiscal_category_id'] = kwargs.get(
            'fiscal_category_id')

        result.update(fp_rule_obj.with_context(context).apply_fiscal_mapping(
            result, **kwargs))
        fiscal_position = result['value'].get('fiscal_position')
        product_id = kwargs.get('product_id')
        if product_id and fiscal_position:
            obj_fposition = self.env['account.fiscal.position'].browse(
                fiscal_position)
            obj_product = self.env['product.product'].browse(product_id)
            context.update({
                'fiscal_type': obj_product.fiscal_type,
                'type_tax_use': 'purchase', 'product_id': product_id})
            taxes = obj_product.supplier_taxes_id
            tax_ids = obj_fposition.with_context(context).map_tax(taxes)
            result['value']['taxes_id'] = tax_ids
        return result

    @api.multi
    def onchange_product_id(self, pricelist_id, product_id, qty, uom_id,
                            partner_id, date_order=False,
                            fiscal_position_id=False, date_planned=False,
                            name=False, price_unit=False, state='draft'):
        """Método para implementar a inclusão dos campos categoria fiscal
        e posição fiscal de acordo com valores padrões e regras de posicões
        fiscais
        """
        ctx = dict(self.env.context)
        company_id = ctx.get('company_id')
        parent_fiscal_category_id = ctx.get('parent_fiscal_category_id')

        result = {'value': {}}

        if product_id and parent_fiscal_category_id and partner_id:
            kwargs = {
                'company_id': company_id,
                'product_id': product_id,
                'partner_id': partner_id,
                'partner_invoice_id': partner_id,
                'fiscal_category_id': parent_fiscal_category_id,
                'context': ctx,
            }
            result.update(self._fiscal_position_map(result, **kwargs))

            obj_product = self.env['product.product'].browse(product_id)
            ctx.update({'fiscal_type': obj_product.fiscal_type,
                        'type_tax_use': 'purchase'})

        result_super = super(PurchaseOrderLine, self).onchange_product_id(
            pricelist_id, product_id, qty, uom_id, partner_id,
            date_order, fiscal_position_id, date_planned, name, price_unit,
            state)
        result_super['value'].update(result['value'])
        return result_super

    @api.multi
    def onchange_product_uom(self, pricelist_id, product_id, qty, uom_id,
                             partner_id, date_order=False,
                             fiscal_position_id=False, date_planned=False,
                             name=False, price_unit=False, state='draft'):
        """Método adaptado para a nova API já que este mesmo chama o método
        onchange_product_id
        """
        return super(PurchaseOrderLine, self).onchange_product_uom(
            pricelist_id, product_id, qty, uom_id, partner_id, date_order,
            fiscal_position_id, date_planned, name, price_unit, state)

    @api.onchange('fiscal_category_id', 'fiscal_position')
    def onchange_fiscal(self):
        result = {'value': {}}
        if (self.product_id and self.order_id.company_id or
                self.order_id.partner_id and self.fiscal_category_id):
            kwargs = {
                'company_id': self.order_id.company_id.id,
                'product_id': self.product_id.id,
                'partner_id': self.order_id.partner_id.id,
                'fiscal_category_id': self.fiscal_category_id.id,
                'taxes_id': [(6, 0, self.taxes_id.ids)],
            }
            result = self._fiscal_position_map(result, **kwargs)
            kwargs.update({
                'fiscal_category_id': self.fiscal_category_id.id,
                'fiscal_position': self.fiscal_position.id,
                'taxes_id': [(6, 0, self.taxes_id.ids)],
            })
            self.update(result['value'])
