#
# Copyright (C) 2016 by frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from django.db import migrations, models
from django.db.models import F, Q
import django.utils.timezone

from freppledb.common.fields import JSONField

class Migration(migrations.Migration):

  dependencies = [
    ('input', '0005_new_data_model'),
  ]

  operations = [
    migrations.RunSQL(
      '''
      insert into operationplan
        (type, id, lastmodified, source, reference, status, quantity, 
		 startdate, enddate, criticality, item_id, location_id, supplier_id, 
		 name)
      select
         'PO', id, lastmodified, source, reference, status, quantity, 
		 startdate, enddate, criticality, item_id, location_id, supplier_id, 
         'Purchase ' || item_id || ' @ ' || location_id || '  from ' || supplier_id
      from purchase_order
      ''',
      '''
      insert into purchase_order
        (id, lastmodified, source, reference, status, quantity, startdate, 
		 enddate, criticality, item_id, location_id, supplier_id)
      select
         id, lastmodified, source, reference, status, quantity, startdate, 
		 enddate, criticality, item_id, location_id, supplier_id
      from operationplan
      where type = 'PO'
      '''
    ),
    migrations.RunSQL(
      '''
      insert into operationplan
        (type, id, lastmodified, source, reference, status, quantity, startdate, 
		 enddate, criticality, item_id, origin_id, destination_id, name)
      select
         'DO', id, lastmodified, source, reference, status, quantity, startdate, 
		 enddate, criticality, item_id, origin_id, destination_id,
		 'Ship ' || item_id || ' from ' || origin_id || ' to ' || destination_id
      from distribution_order
      ''',
      '''
      insert into distribution_order
        (id, lastmodified, source, reference, status, quantity, startdate, enddate, 
		 criticality, item_id, origin_id, destination_id)
      select
       id, lastmodified, source, reference, status, quantity, startdate, enddate,
	   criticality, item_id, origin_id, destination_id from operationplan
      where type = 'DO'
      '''
    ),
    migrations.RunSQL(
      "update operationplan set type = 'MO' where type is null or type = ''",
      "delete from operationplan where type <> 'MO'"
    ),
    migrations.RemoveField(
      model_name='distributionorder',
      name='destination',
    ),
    migrations.RemoveField(
      model_name='distributionorder',
      name='item',
    ),
    migrations.RemoveField(
      model_name='distributionorder',
      name='origin',
    ),
    migrations.RemoveField(
      model_name='purchaseorder',
      name='item',
    ),
    migrations.RemoveField(
      model_name='purchaseorder',
      name='location',
    ),
    migrations.RemoveField(
      model_name='purchaseorder',
      name='supplier',
    ),
    migrations.CreateModel(
      name='DeliveryOrder',
      fields=[
      ],
      options={
        'verbose_name': 'customer shipment',
        'proxy': True,
        'verbose_name_plural': 'customer shipments',
      },
      bases=('input.operationplan',),
    ),
    migrations.CreateModel(
      name='ManufacturingOrder',
      fields=[
      ],
      options={
        'verbose_name': 'manufacturing order',
        'proxy': True,
        'verbose_name_plural': 'manufacturing orders',
      },
      bases=('input.operationplan',),
    ),
    migrations.DeleteModel(
      name='DistributionOrder',
    ),
    migrations.DeleteModel(
      name='PurchaseOrder',
    ),
    migrations.CreateModel(
      name='DistributionOrder',
      fields=[],
      options={
        'verbose_name': 'distribution order',
        'proxy': True,
        'verbose_name_plural': 'distribution orders',
      },
      bases=('input.operationplan',),
    ),
    migrations.CreateModel(
      name='PurchaseOrder',
      fields=[],
      options={
        'verbose_name': 'purchase order',
        'proxy': True,
        'verbose_name_plural': 'purchase orders',
      },
      bases=('input.operationplan',),
    ),

    # A buffer is recognized by an item and location, and they automatically
    # get assigned a name.
#     migrations.RunSQL(
#       '''
#       TODO update buffer name
#       ''',
#       migrations.RunSQL.noop
#     ),

  ]
