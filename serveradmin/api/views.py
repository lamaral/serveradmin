from operator import itemgetter

from django.template.response import TemplateResponse
from django.core.exceptions import (
    SuspiciousOperation,
    PermissionDenied,
    ValidationError,
)
from django.contrib.auth.decorators import login_required
from django.contrib.admindocs.utils import trim_docstring, parse_docstring

from adminapi.filters import FilterValueError, filter_from_obj
from serveradmin.api import ApiError, AVAILABLE_API_FUNCTIONS
from serveradmin.api.decorators import api_view
from serveradmin.api.utils import build_function_description
from serveradmin.dataset import Query   # TODO: Don't access it from here
from serveradmin.dataset.commit import commit_changes
from serveradmin.dataset.create import create_server
from serveradmin.serverdb.query_filterer import QueryFilterer
from serveradmin.serverdb.query_materializer import QueryMaterializer


class StringEncoder(object):
    def loads(self, x):
        return x

    def dumps(self, x):
        return x

    def load(self, file):
        return file.read()

    def dump(self, val, file):
        return file.write(val)


@login_required
def doc_functions(request):
    group_list = []
    for group_name, functions in AVAILABLE_API_FUNCTIONS.items():
        function_list = []
        for name, function in functions.items():
            heading, body, metadata = parse_docstring(function.__doc__)
            body = trim_docstring(body)
            function_list.append({
                'name': name,
                'description': build_function_description(function),
                'docstring': trim_docstring(
                    '{0}\n\n{1}'.format(heading, body)
                ),
            })
        function_list.sort(key=itemgetter('name'))

        group_list.append({
            'name': group_name,
            'function_list': function_list
        })
    group_list.sort(key=itemgetter('name'))
    return TemplateResponse(request, 'api/list_functions.html', {
        'group_list': group_list
    })


@api_view
def dataset_query(request, app, data):
    try:
        if 'filters' not in data or not isinstance(data['filters'], dict):
            raise SuspiciousOperation('Filters must be a dictionary')
        filters = {}
        for attr, filter_obj in data['filters'].items():
            filters[attr] = filter_from_obj(filter_obj)

        # Empty list means query all attributes to the older versions of
        # the adminapi.
        if not data.get('restrict'):
            restrict = None
        else:
            restrict = data['restrict']

        order_by = data.get('order_by')

        filterer = QueryFilterer(filters)
        materializer = QueryMaterializer(filterer, restrict, order_by)

        return {
            'status': 'success',
            'result': list(materializer),
        }
    except (FilterValueError, ValidationError) as error:
        return {
            'status': 'error',
            'type': 'ValueError',
            'message': str(error),
        }


@api_view
def dataset_commit(request, app, data):
    try:
        if 'changes' not in data or 'deleted' not in data:
            raise SuspiciousOperation('Invalid changes')

        skip_validation = bool(data.get('skip_validation', False))
        force_changes = bool(data.get('force_changes', False))

        # Convert keys back to integers (json doesn't handle integer keys)
        changes = {}
        for server_id, change in data['changes'].items():
            changes[int(server_id)] = change

        commit = {'deleted': data['deleted'], 'changes': changes}
        commit_changes(commit, skip_validation, force_changes, app=app)

        return {
            'status': 'success',
        }
    except ValidationError as error:
        return {
            'status': 'error',
            'type': error.__class__.__name__,
            'message': str(error),
        }


@api_view
def dataset_create(request, app, data):
    try:
        required = [
            'attributes',
            'skip_validation',
            'fill_defaults',
            'fill_defaults_all',
        ]
        if not all(key in data for key in required):
            raise SuspiciousOperation('Invalid create request')
        if not isinstance(data['attributes'], dict):
            raise SuspiciousOperation('Attributes must be a dictionary')

        create_server(
            data['attributes'],
            data['skip_validation'],
            data['fill_defaults'],
            data['fill_defaults_all'],
            app=app,
        )

        return {
            'status': 'success',
            'result': Query(filters={
                'hostname': data['attributes']['hostname']
            }).get_results(),
        }
    except ValidationError as error:
        return {
            'status': 'error',
            'type': error.__class__.__name__,
            'message': str(error),
        }


@api_view
def api_call(request, app, data):
    try:
        if not all(x in data for x in ('group', 'name', 'args', 'kwargs')):
            raise SuspiciousOperation('Invalid API call')

        allowed_methods = app.allowed_methods.splitlines()
        method_name = u'{0}.{1}'.format(data['group'], data['name'])
        if not app.superuser and method_name not in allowed_methods:
            raise PermissionDenied(
                'Method {0} not allowed'.format(method_name)
            )

        try:
            fn = AVAILABLE_API_FUNCTIONS[data['group']][data['name']]
        except KeyError:
            raise ApiError('No such function')

        retval = fn(*data['args'], **data['kwargs'])
        return {
            'status': 'success',
            'retval': retval,
        }
    except ApiError as error:
        return {
            'status': 'error',
            'type': 'ApiError',
            'message': str(error),
        }
