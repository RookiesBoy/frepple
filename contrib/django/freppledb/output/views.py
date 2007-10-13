#
# Copyright (C) 2007 by Johan De Taeye
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser
# General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA
#

# file : $URL$
# revision : $LastChangedRevision$  $LastChangedBy$
# date : $LastChangedDate$
# email : jdetaeye@users.sourceforge.net

from datetime import date, datetime

from django.shortcuts import render_to_response
from django.contrib.admin.views.decorators import staff_member_required
from django.template import RequestContext, loader
from django.db import connection
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.conf import settings
from django.utils.translation import ugettext_lazy as _

from freppledb.input.models import Buffer, Operation, Resource, Item, Forecast
from freppledb.output.models import DemandPegging, FlowPlan, Problem, OperationPlan, LoadPlan, Demand
from freppledb.dbutils import *
from freppledb.report import TableReport, ListReport


class BufferReport(TableReport):
  '''
  A report showing the inventory profile of buffers.
  '''
  template = 'output/buffer.html'
  title = _('Inventory Report')
  basequeryset = Buffer.objects.all()
  rows = (
    ('buffer', {'filter': 'name__icontains', 'order_by': 'name', 'title':_('buffer')}),
    ('item', {'filter': 'item__name__icontains', 'title':_('item')}),
    ('location', {'filter': 'location__name__icontains', 'title':_('location')}),
    )
  crosses = (
    ('startoh', {'title':_('start inventory'),}),
    ('consumed', {'title':_('consumed'),}),
    ('produced', {'title':_('produced'),}),
    ('endoh', {'title':_('end inventory'),}),
    )
  columns = (
    ('bucket', {'title':_('bucket')}),
    )

  @staticmethod
  def resultquery(basesql, baseparams, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connection.cursor()
    query = '''
      select buf.name as row1, buf.item_id as row2, buf.location_id as row3, buf.onhand as row4,
             d.bucket as col1, d.startdate as col2, d.enddate as col3,
             coalesce(sum(%s),0.0) as consumed,
             coalesce(-sum(%s),0.0) as produced
        from (%s) buf
        -- Multiply with buckets
        cross join (
             select %s as bucket, %s_start as startdate, %s_end as enddate
             from dates
             where day_start >= '%s' and day_start <= '%s'
             group by %s, %s_start, %s_end
             ) d
        -- Consumed and produced quantities
        left join out_flowplan
        on buf.name = out_flowplan.thebuffer
        and d.startdate <= out_flowplan.flowdate
        and d.enddate > out_flowplan.flowdate
        -- Grouping and sorting
        group by buf.name, buf.item_id, buf.location_id, buf.onhand, d.bucket, d.startdate, d.enddate
        order by %s, d.startdate
      ''' % (sql_max('out_flowplan.quantity','0.0'),sql_min('out_flowplan.quantity','0.0'),
        basesql,connection.ops.quote_name(bucket),bucket,bucket,startdate,enddate,
        connection.ops.quote_name(bucket),bucket,bucket,sortsql)
    cursor.execute(query, baseparams)

    # Build the python result
    prevbuf = None
    rowset = []
    for row in cursor.fetchall():
      if row[0] != prevbuf:
        if prevbuf: yield rowset
        rowset = []
        prevbuf = row[0]
        endoh = float(row[3])
      startoh = endoh   # @todo the starting onhand isn't right for the first bucket...
      endoh += float(row[7] - row[8])
      rowset.append( {
        'buffer': row[0],
        'item': row[1],
        'location': row[2],
        'bucket': row[4],
        'startdate': python_date(row[5]),
        'enddate': python_date(row[6]),
        'startoh': startoh,
        'produced': row[7],
        'consumed': row[8],
        'endoh': endoh,
        } )
    if prevbuf: yield rowset


class DemandReport(TableReport):
  '''
  A report showing the independent demand for each item.
  '''
  template = 'output/demand.html'
  title = _('Demand Report')
  basequeryset = Item.objects.extra(where=('name in (select item_id from demand)',))
  rows = (
    ('item',{'filter': 'name__icontains', 'order_by': 'name', 'title':_('item')}),
    )
  crosses = (
    ('demand',{'title':_('demand')}),
    ('supply',{'title':_('supply')}),
    ('backlog',{'title':_('backlog')}),
    )
  columns = (
    ('bucket',{'title':_('bucket')}),
    )

  @staticmethod
  def resultquery(basesql, baseparams, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connection.cursor()
    query = '''
        select x.name as row1,
               x.bucket as col1, x.startdate as col2, x.enddate as col3,
               coalesce(sum(demand.quantity),0),
               min(x.planned)
        from (
          select items.name as name,
                 d.bucket as bucket, d.startdate as startdate, d.enddate as enddate,
                 coalesce(sum(pln.quantity),0) as planned
          from (%s) items
          -- Multiply with buckets
          cross join (
               select %s as bucket, %s_start as startdate, %s_end as enddate
               from dates
               where day_start >= '%s' and day_start <= '%s'
               group by %s, %s_start, %s_end
               ) d
          -- Planned quantity
          left join (
            select item_id as item_id, out_demand.plandate as plandate, out_demand.planquantity as quantity
            from out_demand
            inner join demand
            on out_demand.demand = demand.name
            ) pln
          on items.name = pln.item_id
          and d.startdate <= pln.plandate
          and d.enddate > pln.plandate
          -- Grouping
          group by items.name, d.bucket, d.startdate, d.enddate
        ) x
        -- Requested quantity
        left join demand
        on x.name = demand.item_id
        and x.startdate <= demand.due
        and x.enddate > demand.due
        -- Ordering and grouping
        group by x.name, x.bucket, x.startdate, x.enddate
        order by %s, x.startdate
       ''' % (basesql,connection.ops.quote_name(bucket),bucket,bucket,
       startdate,enddate,connection.ops.quote_name(bucket),bucket,bucket,sortsql)
    cursor.execute(query,baseparams)

    # Build the python result
    previtem = None
    rowset = []
    for row in cursor.fetchall():
      if row[0] != previtem:
        if previtem: yield rowset
        rowset = []
        previtem = row[0]
        backlog = 0         # @todo Setting the backlog to 0 is not correct: it may be non-zero from the plan before the start date
      backlog += row[4] - row[5]
      rowset.append( {
        'item': row[0],
        'bucket': row[1],
        'startdate': python_date(row[2]),
        'enddate': python_date(row[3]),
        'demand': row[4],
        'supply': row[5],
        'backlog': backlog,
        } )
    if previtem: yield rowset


class ForecastReport(TableReport):
  '''
  A report allowing easy editing of forecast numbers.
  '''
  template = 'output/forecast.html'
  title = _('Forecast Report')
  basequeryset = Forecast.objects.all()
  rows = (
    ('forecast',{'filter': 'name__icontains', 'order_by': 'name', 'title':_('forecast')}),
    ('item',{'filter': 'item__name__icontains', 'title':_('item')}),
    ('customer',{'filter': 'customer__name__icontains', 'title':_('customer')}),
    )
  crosses = (
    ('demand',{'title': _('gross forecast')}),
    ('planned',{'title':_('planned forecast')}),
    )
  columns = (
    ('bucket',{'title':_('bucket')}),
    )

  @staticmethod
  def resultquery(basesql, baseparams, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connection.cursor()
    query = '''
        select x.name as row1, x.item_id as row2, x.customer_id as row3,
               x.bucket as col1, x.startdate as col2, x.enddate as col3,
               min(x.demand),
               coalesce(sum(out_demand.planquantity),0)
        from (
          select fcst.name as name, fcst.item_id as item_id, fcst.customer_id as customer_id,
                 d.bucket as bucket, d.startdate as startdate, d.enddate as enddate,
                 coalesce(sum(forecastdemand.quantity * %s / %s),0) as demand
          from (%s) fcst
          -- Multiply with buckets
          cross join (
               select %s as bucket, %s_start as startdate, %s_end as enddate
               from dates
               where day_start >= '%s' and day_start <= '%s'
               group by %s, %s_start, %s_end
               ) d
          -- Total forecast demand quantity
          left join forecastdemand
          on fcst.name = forecastdemand.forecast_id
          and forecastdemand.enddate >= d.startdate
          and forecastdemand.startdate <= d.enddate
          -- Grouping
          group by fcst.name, fcst.item_id, fcst.customer_id,
                 d.bucket, d.startdate, d.enddate
          ) x
        -- Planned quantity
        left join out_demand
        on x.name = out_demand.demand
        and x.startdate <= out_demand.plandatetime
        and x.enddate > out_demand.plandatetime
        -- Ordering and grouping
        group by x.name, x.item_id, x.customer_id,
           x.bucket, x.startdate, x.enddate
        order by %s, x.startdate
        ''' % (sql_overlap('forecastdemand.startdate','forecastdemand.enddate','d.startdate','d.enddate'),
         sql_datediff('forecastdemand.enddate','forecastdemand.startdate'),
         basesql,connection.ops.quote_name(bucket),bucket,bucket,startdate,enddate,
         connection.ops.quote_name(bucket),bucket,bucket,sortsql)
    cursor.execute(query, baseparams)

    # Build the python result
    prevfcst = None
    rowset = []
    for row in cursor.fetchall():
      if row[0] != prevfcst:
        if prevfcst: yield rowset
        rowset = []
        prevfcst = row[0]
      rowset.append( {
        'forecast': row[0],
        'item': row[1],
        'customer': row[2],
        'bucket': row[3],
        'startdate': python_date(row[4]),
        'enddate': python_date(row[5]),
        'demand': row[6],
        'planned': row[7],
        } )
    if prevfcst: yield rowset


class ResourceReport(TableReport):
  '''
  A report showing the loading of each resource.
  '''
  template = 'output/resource.html'
  title = _('Resource Report')
  basequeryset = Resource.objects.all()
  rows = (
    ('resource',{'filter': 'name__icontains', 'order_by': 'name', 'title':_('resource')}),
    ('location',{'filter': 'location__name__icontains', 'title':_('location')}),
    )
  crosses = (
    ('available',{'title':_('available')}),
    ('load',{'title':_('load')}),
    ('utilization',{'title':_('utilization %'),}),
    )
  columns = (
    ('bucket',{'title':_('bucket')}),
    )

  @staticmethod
  def resultquery(basesql, baseparams, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connection.cursor()
    query = '''
       select x.name as row1, x.location_id as row2,
             x.bucket as col1, x.startdate as col2, x.enddate as col3,
             min(x.available),
             coalesce(sum(loaddata.usagefactor * %s), 0) as loading
       from (
         select res.name as name, res.location_id as location_id,
               d.bucket as bucket, d.startdate as startdate, d.enddate as enddate,
               coalesce(sum(bucket.value * %s),0) as available
         from (%s) res
         -- Multiply with buckets
         cross join (
              select %s as bucket, %s_start as startdate, %s_end as enddate
              from dates
              where day_start >= '%s' and day_start <= '%s'
              group by %s, %s_start, %s_end
              ) d
         -- Available capacity
         left join bucket
         on res.maximum_id = bucket.calendar_id
         and d.startdate <= bucket.enddate
         and d.enddate >= bucket.startdate
         -- Grouping
         group by res.name, res.location_id, d.bucket, d.startdate, d.enddate
       ) x
       -- Load data
       left join (
         select %s as resource_id, startdatetime, enddatetime, quantity as usagefactor
         from out_loadplan
         ) loaddata
       on x.name = loaddata.resource_id
       and x.startdate <= loaddata.enddatetime
       and x.enddate >= loaddata.startdatetime
       -- Grouping and ordering
       group by x.name, x.location_id, x.bucket, x.startdate, x.enddate
       order by %s, x.startdate
       ''' % ( sql_overlap('loaddata.startdatetime','loaddata.enddatetime','x.startdate','x.enddate'),
         sql_overlap('bucket.startdate','bucket.enddate','d.startdate','d.enddate'),
         basesql,connection.ops.quote_name(bucket),bucket,bucket,startdate,enddate,
         connection.ops.quote_name(bucket),bucket,bucket,connection.ops.quote_name('resource'),sortsql)
    cursor.execute(query, baseparams)

    # Build the python result
    prevres = None
    rowset = []
    for row in cursor.fetchall():
      if row[0] != prevres:
        count = 0
        if prevres: yield rowset
        rowset = []
        prevres = row[0]
      if row[5] != 0: util = row[6] / row[5] * 100
      else: util = 0
      count += 1
      rowset.append( {
        'resource': row[0],
        'location': row[1],
        'bucket': row[2],
        'startdate': python_date(row[3]),
        'enddate': python_date(row[4]),
        'available': row[5],
        'load': row[6],
        'utilization': util,
        } )
    if prevres: yield rowset


class OperationReport(TableReport):
  '''
  A report showing the planned starts of each operation.
  '''
  template = 'output/operation.html'
  title = _('Operation Report')
  basequeryset = Operation.objects.all()
  rows = (
    ('operation',{'filter': 'name__icontains', 'order_by': 'name', 'title':_('operation')}),
    )
  crosses = (
    ('frozen_start', {'title':_('frozen starts'),}),
    ('total_start', {'title':_('total starts'),}),
    ('frozen_end', {'title':_('frozen ends'),}),
    ('total_end', {'title':_('total ends'),}),
    )
  columns = (
    ('bucket',{'title':_('bucket')}),
    )

  @staticmethod
  def resultquery(basesql, baseparams, bucket, startdate, enddate, sortsql='1 asc'):
    # Run the query
    cursor = connection.cursor()
    query = '''
        select x.row1, x.col1, x.col2, x.col3,
          min(x.frozen_start), min(x.total_start),
          coalesce(sum(case o2.locked when %s then o2.quantity else 0 end),0),
          coalesce(sum(o2.quantity),0)
        from (
          select oper.name as row1,
               d.bucket as col1, d.startdate as col2, d.enddate as col3,
               coalesce(sum(case o1.locked when %s then o1.quantity else 0 end),0) as frozen_start,
               coalesce(sum(o1.quantity),0) as total_start
          from (%s) oper
          -- Multiply with buckets
          cross join (
               select %s as bucket, %s_start as startdate, %s_end as enddate
               from dates
               where day_start >= '%s' and day_start <= '%s'
               group by %s, %s_start, %s_end
               ) d
          -- Planned and frozen quantity, based on start date
          left join out_operationplan o1
          on oper.name = o1.operation
          and d.startdate <= o1.startdate
          and d.enddate > o1.startdate
          -- Grouping
          group by oper.name, d.bucket, d.startdate, d.enddate
        ) x
        -- Planned and frozen quantity, based on end date
        left join out_operationplan o2
        on x.row1 = o2.operation
        and x.col2 <= o2.enddate
        and x.col3 > o2.enddate
        -- Grouping and ordering
        group by x.row1, x.col1, x.col2, x.col3
        order by %s, x.col2
      ''' % (sql_true(),sql_true(),basesql,
      connection.ops.quote_name(bucket),bucket,bucket,startdate,enddate,
      connection.ops.quote_name(bucket),bucket,bucket,sortsql)
    cursor.execute(query, baseparams)

    # Convert the SQl results to python
    prevoper = None
    rowset = []
    for row in cursor.fetchall():
      if row[0] != prevoper:
        if prevoper: yield rowset
        rowset = []
        prevoper = row[0]
      rowset.append( {
        'operation': row[0],
        'bucket': row[1],
        'startdate': python_date(row[2]),
        'enddate': python_date(row[3]),
        'frozen_start': row[4],
        'total_start': row[5],
        'frozen_end': row[6],
        'total_end': row[7],
        } )
    if prevoper: yield rowset


class pathreport:
  '''
  A report showing the upstream supply path or following downstream a
  where-used path.
  The supply path report shows all the materials, operations and resources
  used to make a certain item.
  The where-used report shows all the materials and operations that use
  a specific item.
  '''

  @staticmethod
  def getPath(type, entity, downstream):
    '''
    A generator function that recurses upstream or downstream in the supply
    chain.
    @todo The current code only supports 1 level of super- or sub-operations.
    @todo When the supply chain contains loops this function wont work fine.
    '''
    from decimal import Decimal
    from django.core.exceptions import ObjectDoesNotExist
    if type == 'buffer':
      # Find the buffer
      try: root = [ (0, Buffer.objects.get(name=entity), None, None, None, Decimal(1)) ]
      except ObjectDoesNotExist: raise Http404, "buffer %s doesn't exist" % entity
    elif type == 'item':
      # Find the item
      try:
        root = [ (0, r, None, None, None, Decimal(1)) for r in Buffer.objects.filter(item=entity) ]
      except ObjectDoesNotExist: raise Http404, "item %s doesn't exist" % entity
    elif type == 'operation':
      # Find the operation
      try: root = [ (0, None, None, Operation.objects.get(name=entity), None, Decimal(1)) ]
      except ObjectDoesNotExist: raise Http404, "operation %s doesn't exist" % entity
    elif type == 'resource':
      # Find the resource
      try: root = Resource.objects.get(name=entity)
      except ObjectDoesNotExist: raise Http404, "resource %s doesn't exist" % entity
      root = [ (0, None, None, i.operation, None, Decimal(1)) for i in root.loads.all() ]
    else:
      raise Http404, "invalid entity type %s" % type

    # Note that the root to start with can be either buffer or operation.
    while len(root) > 0:
      level, curbuffer, curprodflow, curoperation, curconsflow, curqty = root.pop()
      yield {
        'buffer': curbuffer,
        'producingflow': curprodflow,
        'operation': curoperation,
        'level': level,
        'consumingflow': curconsflow,
        'cumquantity': curqty,
        }

      if downstream:
        # Find all operations consuming from this buffer...
        if curbuffer:
          start = [ (i, i.operation) for i in curbuffer.flows.filter(quantity__lt=0).select_related(depth=1) ]
        else:
          start = [ (None, curoperation) ]
        for cons_flow, curoperation in start:
          if not cons_flow and not curoperation: continue
          # ... and pick up the buffer they produce into
          ok = False

          # Push the next buffer on the stack, based on current operation
          for prod_flow in curoperation.flows.filter(quantity__gt=0).select_related(depth=1):
            ok = True
            root.append( (level+1, prod_flow.thebuffer, prod_flow, curoperation, cons_flow, curqty / prod_flow.quantity * (cons_flow and cons_flow.quantity * -1 or 1)) )

          # Push the next buffer on the stack, based on super-operations
          for x in curoperation.superoperations.select_related(depth=1):
            for prod_flow in x.suboperation.flows.filter(quantity__gt=0):
              ok = True
              root.append( (level+1, prod_flow.thebuffer, prod_flow, curoperation, cons_flow, curqty / prod_flow.quantity * (cons_flow and cons_flow.quantity * -1 or 1)) )

          # Push the next buffer on the stack, based on sub-operations
          for x in curoperation.suboperations.select_related(depth=1):
            for prod_flow in x.operation.flows.filter(quantity__gt=0):
              ok = True
              root.append( (level+1, prod_flow.thebuffer, prod_flow, curoperation, cons_flow, curqty / prod_flow.quantity * (cons_flow and cons_flow.quantity * -1 or 1)) )

          if not ok and cons_flow:
            # No producing flow found: there are no more buffers downstream
            root.append( (level+1, None, None, curoperation, cons_flow, curqty * cons_flow.quantity * -1) )

      else:
        # Find all operations producing into this buffer...
        if curbuffer:
          if curbuffer.producing:
            start = [ (i, i.operation) for i in curbuffer.producing.flows.filter(quantity__gt=0).select_related(depth=1) ]
          else:
            start = []
        else:
          start = [ (None, curoperation) ]
        for prod_flow, curoperation in start:
          if not prod_flow and not curoperation: continue
          # ... and pick up the buffer they produce into
          ok = False

          # Push the next buffer on the stack, based on current operation
          for cons_flow in curoperation.flows.filter(quantity__lt=0).select_related(depth=1):
            ok = True
            root.append( (level-1, cons_flow.thebuffer, prod_flow, cons_flow.operation, cons_flow, curqty / (prod_flow and prod_flow.quantity or 1) * cons_flow.quantity * -1) )

          # Push the next buffer on the stack, based on super-operations
          for x in curoperation.superoperations.select_related(depth=1):
            for cons_flow in x.suboperation.flows.filter(quantity__lt=0):
              ok = True
              root.append( (level-1, cons_flow.thebuffer, prod_flow, cons_flow.operation, cons_flow, curqty / (prod_flow and prod_flow.quantity or 1) * cons_flow.quantity * -1) )

          # Push the next buffer on the stack, based on sub-operations
          for x in curoperation.suboperations.select_related(depth=1):
            for cons_flow in x.operation.flows.filter(quantity__lt=0):
              ok = True
              root.append( (level-1, cons_flow.thebuffer, prod_flow, cons_flow.operation, cons_flow, curqty / (prod_flow and prod_flow.quantity or 1) * cons_flow.quantity * -1) )

          if not ok and prod_flow:
            # No consuming flow found: there are no more buffers upstream
            ok = True
            root.append( (level-1, None, prod_flow, prod_flow.operation, None, curqty / prod_flow.quantity) )


  @staticmethod
  @staff_member_required
  def viewdownstream(request, type, entity):
    return render_to_response('path.html', RequestContext(request,{
       'title': '%s %s %s' % (_("Where-used report for"),type, entity),
       'supplypath': pathreport.getPath(type, entity, True),
       'type': type,
       'entity': entity,
       'downstream': True,
       }))


  @staticmethod
  @staff_member_required
  def viewupstream(request, type, entity):
    return render_to_response('path.html', RequestContext(request,{
       'title': '%s %s %s' % (_("Supply path report for"),type, entity),
       'supplypath': pathreport.getPath(type, entity, False),
       'type': type,
       'entity': entity,
       'downstream': False,
       }))


class PeggingReport(ListReport):
  '''
  A list report to show peggings.
  '''
  template = 'output/pegging.html'
  title = _("Pegging Report")
  reset_crumbs = False
  basequeryset = DemandPegging.objects.all()
  rows = (
    ('demand', {'filter': 'demand__icontains', 'filter_size': 15, 'title':_('demand')}),
    ('buffer', {'filter': 'buffer__icontains', 'title':_('buffer')}),
    ('depth', {'filter': 'depth', 'filter_size': 2, 'title':_('depth')}),
    ('cons_date', {'title':_('consuming date')}),
    ('prod_date', {'title':_('producing date')}),
    ('cons_operationplan', {'title':_('consuming operationplan')}),
    ('prod_operationplan', {'title':_('producing operationplan')}),
    ('quantity_demand', {'title':_('quantity demand')}),
    ('quantity_buffer', {'title':_('quantity buffer')}),
    ('pegged', {'title':_('pegged')}),
    )


class FlowPlanReport(ListReport):
  '''
  A list report to show flowplans.
  '''
  template = 'output/flowplan.html'
  title = _("Inventory Detail Report")
  reset_crumbs = False
  basequeryset = FlowPlan.objects.extra(
    select={'operation':'out_operationplan.operation'},
    where=['out_operationplan.identifier = out_flowplan.operationplan'],
    tables=['out_operationplan'])
  rows = (
    ('thebuffer', {'filter': 'thebuffer__icontains', 'title': _('buffer')}),
    # @todo Eagerly awaiting the Django queryset refactoring to be able to filter on the operation field.
    # ('operation', {'filter': 'operation__icontains', 'title':_('operation')}),
    ('operation', {'sort': False, 'title':_('operation')}),
    ('quantity', {'title':_('quantity')}),
    ('flowdatetime', {'title': _('date')}),
    ('onhand', {'title':_('onhand')}),
    ('operationplan', {'filter': 'operationplan__icontains', 'title':_('operationplan')}),
    )


class ProblemReport(ListReport):
  '''
  A list report to show problems.
  '''
  template = 'output/problem.html'
  title = _("Problem Report")
  basequeryset = Problem.objects.all()
  rows = (
    ('entity', {'filter': 'entity__icontains', 'title':_('entity')}),
    ('name', {'filter': 'name__icontains', 'title':_('name')}),
    ('description', {'filter': 'description__icontains', 'filter_size': 30, 'title':_('description')}),
    ('startdatetime', {'title': _('startdate')}),
    ('enddatetime', {'title': _('enddate')}),
    ('weight', {'title':_('weight')}),
    )


class OperationPlanReport(ListReport):
  '''
  A list report to show operationplans.
  '''
  template = 'output/operationplan.html'
  title = _("Operationplan Detail Report")
  reset_crumbs = False
  basequeryset = OperationPlan.objects.extra(
    select={'fcst_or_actual':'demand in (select distinct name from forecast)'}
    )
  rows = (
    ('identifier', {'filter': 'identifier__icontains', 'title': _('operationplan')}),
    ('demand', {'filter': 'demand__icontains', 'filter_size': 15, 'title':_('demand')}),
    ('operation', {'filter': 'operation__icontains', 'filter_size': 15, 'title':_('operation')}),
    ('quantity', {'title':_('quantity')}),
    ('startdatetime', {'title': _('startdate')}),
    ('enddatetime', {'title': _('enddate')}),
    ('locked', {'title':_('locked')}),
    ('owner', {'title':_('owner')}),
    )


class DemandPlanReport(ListReport):
  '''
  A list report to show delivery plans for demand.
  '''
  template = 'output/demandplan.html'
  title = _("Demand Plan Detail")
  reset_crumbs = False
  basequeryset = Demand.objects.extra(
    select={'item':'demand.item_id'},
    where=['demand.name = out_demand.demand'],
    tables=['demand'])
  rows = (
    ('demand', {'filter': 'demand__icontains', 'title': _('Demand')}),
    # @todo Eagerly awaiting the Django queryset refactoring to be able to filter on the item field.
    # ('item_id', {'filter': 'item__icontains', 'title':_('item')}),
    ('item', {'sort': False, 'title':_('item')}),
    ('quantity', {'title':_('quantity')}),
    ('planquantity', {'title': _('planned quantity')}),
    ('duedatetime', {'title': _('due date')}),
    ('plandatetime', {'title': _('planned date')}),
    ('operationplan', {'title':_('operationplan')}),
    )


class LoadPlanReport(ListReport):
  '''
  A list report to show loadplans.
  '''
  template = 'output/loadplan.html'
  title = _("Resource Load Detail")
  reset_crumbs = False
  basequeryset = LoadPlan.objects.extra(
    select={'operation':'out_operationplan.operation'},
    where=['out_operationplan.identifier = out_loadplan.operationplan'],
    tables=['out_operationplan'])
  rows = (
    ('resource', {'filter': 'resource__icontains', 'title':_('resource')}),
    # @todo Eagerly awaiting the Django queryset refactoring to be able to filter on the operation field.
    #('operation', {'filter': 'operation__icontains', 'title':_('operation')}),
    ('operation', {'sort': False, 'title':_('operation')}),
    ('startdatetime', {'title': _('startdate')}),
    ('enddatetime', {'title': _('enddate')}),
    ('quantity', {'title':_('quantity')}),
    ('operationplan', {'filter': 'operationplan__icontains', 'title':_('operationplan')}),
    )
