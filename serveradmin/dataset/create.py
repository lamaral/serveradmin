import json
from ipaddress import (
    ip_address,
    IPv4Address,
    IPv6Address,
    IPv4Network,
    IPv6Network,
)

from serveradmin.serverdb.models import (
    ServerObject,
    ChangeCommit,
    ChangeAdd,
)
from serveradmin.dataset.base import lookups
from serveradmin.dataset.validation import handle_violations, check_attribute_type
from serveradmin.dataset.typecast import typecast
from serveradmin.dataset.exceptions import CommitError
from adminapi.utils.json import json_encode_extra

def create_server(
        attributes,
        skip_validation,
        fill_defaults,
        fill_defaults_all,
        user=None,
        app=None,
    ):

    # Import here to break cyclic imports.
    from serveradmin.iprange.models import IPRange

    if u'hostname' not in attributes:
        raise CommitError(u'Hostname is required')
    if u'servertype' not in attributes:
        raise CommitError(u'Servertype is required')
    if u'project' not in attributes:
        raise CommitError(u'Project is required')
    if u'intern_ip' not in attributes:
        raise CommitError(u'Internal IP (intern_ip) is required')

    for attr in (u'hostname', u'servertype', u'project', u'intern_ip'):
        check_attribute_type(attr, attributes[attr])

    if attributes[u'servertype'] in lookups.servertypes:
        servertype = lookups.servertypes[attributes[u'servertype']]
    else:
        raise CommitError(u'Unknown servertype: ' + attributes[u'servertype'])

    hostname = attributes[u'hostname']
    if isinstance(attributes['intern_ip'], (
        IPv4Address,
        IPv6Address,
        IPv4Network,
        IPv6Network,
    )):
        intern_ip = attributes['intern_ip']
    else:
        intern_ip = ip_address(attributes[u'intern_ip'])
    servertype_id = servertype.pk
    segment_id = attributes.get(u'segment')

    if segment_id:
        check_attribute_type(u'segment', segment_id)
    else:
        try:
            segment_id = IPRange.objects.filter(
                    min__lte=intern_ip,
                    max__gte=intern_ip,
                )[0].segment
        except IndexError:
            raise CommitError('Could not determine segment')

    project_id = attributes.get(u'project')

    real_attributes = attributes.copy()
    for key in (
            u'hostname',
            u'intern_ip',
            u'comment',
            u'servertype',
            u'segment',
            u'project',
        ):
        if key in real_attributes:
            del real_attributes[key]

    violations_regexp = []
    violations_required = []
    for attribute in servertype.attributes:
        stype_attr = lookups.stype_attrs[(servertype.pk, attribute.pk)]

        # Handle not existing attributes (fill defaults, validate require)
        if attribute.pk not in real_attributes:
            if attribute.multi:
                if stype_attr.default in ('', None):
                    real_attributes[attribute.pk] = []
                else:
                    real_attributes[attribute.pk] = _type_cast_default(attribute,
                            stype_attr.default)
            elif stype_attr.required:
                if fill_defaults and stype_attr.default not in ('', None):
                    real_attributes[attribute.pk] = _type_cast_default(
                            attribute,
                            stype_attr.default,
                        )
                else:
                    violations_required.append(attribute.pk)
                    continue
            else:
                if fill_defaults_all and stype_attr.default not in ('', None):
                    real_attributes[attribute.pk] = _type_cast_default(
                            attribute,
                            stype_attr.default,
                        )
                else:
                    continue

        value = real_attributes[attribute.pk]
        check_attribute_type(attribute.pk, value)

        # Validate regular expression
        regexp = stype_attr.regexp
        if attribute.multi:
            if attribute.type == 'string' and regexp:
                for val in value:
                    if not regexp.match(unicode(val)):
                        violations_regexp.append(attribute.pk)
        else:
            if attribute.type == 'string' and regexp and not regexp.match(value):
                violations_regexp.append(attribute.pk)

    # Check for attributes that are not defined on this servertype
    violations_attribs = []
    servertype_attributes = set([a.pk for a in servertype.attributes])
    for attr in real_attributes:
        if attr not in servertype_attributes:
            violations_attribs.append(attr)

    handle_violations(
        skip_validation,
        violations_regexp,
        violations_required,
        violations_attribs,
    )

    server_id = _insert_server(
        hostname,
        intern_ip,
        segment_id,
        servertype_id,
        project_id,
        real_attributes,
    )

    created_server = real_attributes.copy()
    created_server['hostname'] = hostname
    created_server['intern_ip'] = intern_ip

    commit = ChangeCommit.objects.create(app=app, user=user)
    attributes_json = json.dumps(created_server, default=json_encode_extra)
    ChangeAdd.objects.create(
        commit=commit,
        hostname=created_server['hostname'],
        attributes_json=attributes_json,
    )

    return server_id

def _insert_server(hostname, intern_ip, segment_id, servertype_id, project_id, attributes):

    if ServerObject.objects.filter(hostname=hostname).exists():
        raise CommitError(u'Server with that hostname already exists')

    server = ServerObject.objects.create(
        hostname=hostname,
        intern_ip=intern_ip,
        project_id=project_id,
        servertype_id=servertype_id,
        segment_id=segment_id,
    )

    for attr_name, value in attributes.iteritems():
        attribute = lookups.attributes[attr_name]

        if attribute.multi:
            for single_value in value:
                server.add_attribute(attribute, single_value)
        else:
            server.add_attribute(attribute, value)

    server.save()

    return server.server_id

def _type_cast_default(attribute, value):
    if attribute.multi:
        return [
                typecast(attribute, val, force_single=True)
                for val in value.split(',')
            ]
    else:
        return typecast(attribute, value, force_single=True)
