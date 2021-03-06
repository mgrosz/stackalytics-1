# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import operator
import os
import time

import flask
from flask.ext import gravatar as gravatar_ext
from oslo.config import cfg

from dashboard import decorators
from dashboard import helpers
from dashboard import parameters
from dashboard import reports
from dashboard import vault
from stackalytics.openstack.common import log as logging
from stackalytics.processor import config
from stackalytics.processor import utils


# Application objects ---------

app = flask.Flask(__name__)
app.config.from_object(__name__)
app.config.from_envvar('DASHBOARD_CONF', silent=True)
app.register_blueprint(reports.blueprint)

LOG = logging.getLogger(__name__)

conf = cfg.CONF
conf.register_opts(config.OPTS)
logging.setup('dashboard')
LOG.info('Logging enabled')

conf_file = os.getenv('STACKALYTICS_CONF')
if conf_file and os.path.isfile(conf_file):
    conf(default_config_files=[conf_file])
    app.config['DEBUG'] = cfg.CONF.debug
else:
    LOG.warn('Conf file is empty or not exist')


# Handlers ---------

@app.route('/')
@decorators.templated()
def overview():
    pass


@app.route('/widget')
def widget():
    return flask.render_template('widget.html')


@app.errorhandler(404)
@decorators.templated('404.html', 404)
def page_not_found(e):
    pass


# AJAX Handlers ---------

def _get_aggregated_stats(records, metric_filter, keys, param_id,
                          param_title=None, finalize_handler=None):
    param_title = param_title or param_id
    result = dict((c, {'metric': 0, 'id': c}) for c in keys)
    for record in records:
        metric_filter(result, record, param_id)
        result[record[param_id]]['name'] = record[param_title]

    if not finalize_handler:
        finalize_handler = lambda x: x

    response = [finalize_handler(result[r]) for r in result
                if result[r]['metric']]
    response.sort(key=lambda x: x['metric'], reverse=True)
    return response


@app.route('/api/1.0/stats/companies')
@decorators.jsonify('stats')
@decorators.exception_handler()
@decorators.record_filter()
@decorators.aggregate_filter()
def get_companies(records, metric_filter, finalize_handler):
    return _get_aggregated_stats(records, metric_filter,
                                 vault.get_memory_storage().get_companies(),
                                 'company_name')


@app.route('/api/1.0/stats/modules')
@decorators.jsonify('stats')
@decorators.exception_handler()
@decorators.record_filter()
@decorators.aggregate_filter()
def get_modules(records, metric_filter, finalize_handler):
    return _get_aggregated_stats(records, metric_filter,
                                 vault.get_memory_storage().get_modules(),
                                 'module')


@app.route('/api/1.0/stats/engineers')
@decorators.jsonify('stats')
@decorators.exception_handler()
@decorators.record_filter()
@decorators.aggregate_filter()
def get_engineers(records, metric_filter, finalize_handler):
    return _get_aggregated_stats(records, metric_filter,
                                 vault.get_memory_storage().get_user_ids(),
                                 'user_id', 'author_name',
                                 finalize_handler=finalize_handler)


@app.route('/api/1.0/stats/distinct_engineers')
@decorators.jsonify('stats')
@decorators.exception_handler()
@decorators.record_filter()
def get_distinct_engineers(records):
    result = {}
    for record in records:
        result[record['user_id']] = {
            'author_name': record['author_name'],
            'author_email': record['author_email'],
        }
    return result


@app.route('/api/1.0/activity')
@decorators.jsonify('activity')
@decorators.exception_handler()
@decorators.record_filter()
def get_activity_json(records):
    start_record = int(flask.request.args.get('start_record') or 0)
    page_size = int(flask.request.args.get('page_size') or
                    parameters.DEFAULT_RECORDS_LIMIT)
    records_sorted = sorted(records, key=lambda x: x['date'], reverse=True)
    records_sorted = records_sorted[start_record:start_record + page_size]

    result = []
    for record in records_sorted:
        processed_record = helpers.extend_record(record)
        if processed_record:
            result.append(processed_record)

    return result


@app.route('/api/1.0/contribution')
@decorators.jsonify('contribution')
@decorators.exception_handler()
@decorators.record_filter(ignore='metric')
def get_contribution_json(records):
    return helpers.get_contribution_summary(records)


@app.route('/api/1.0/companies')
@decorators.jsonify('companies')
@decorators.exception_handler()
@decorators.record_filter(ignore='company')
def get_companies_json(records):
    query = flask.request.args.get('company_name') or ''
    options = set()
    for record in records:
        name = record['company_name']
        if name in options:
            continue
        if name.lower().find(query.lower()) >= 0:
            options.add(name)
    result = [{'id': helpers.safe_encode(c.lower()), 'text': c}
              for c in sorted(options)]
    return result


@app.route('/api/1.0/modules')
@decorators.jsonify('modules')
@decorators.exception_handler()
@decorators.record_filter(ignore='module')
def get_modules_json(records):
    module_group_index = vault.get_vault()['module_group_index']
    module_id_index = vault.get_vault()['module_id_index']

    modules_set = set()
    for record in records:
        module = record['module']
        if module not in modules_set:
            modules_set.add(module)

    modules_groups_set = set()
    for module in modules_set:
        if module in module_group_index:
            modules_groups_set |= module_group_index[module]

    modules_set |= modules_groups_set

    query = (flask.request.args.get('module_name') or '').lower()
    options = []

    for module in modules_set:
        if module.find(query) >= 0:
            options.append(module_id_index[module])

    return sorted(options, key=operator.itemgetter('text'))


@app.route('/api/1.0/companies/<company_name>')
@decorators.jsonify('company')
def get_company(company_name):
    memory_storage_inst = vault.get_memory_storage()
    for company in memory_storage_inst.get_companies():
        if company.lower() == company_name.lower():
            return {
                'id': company_name,
                'text': memory_storage_inst.get_original_company_name(
                    company_name)
            }
    flask.abort(404)


@app.route('/api/1.0/modules/<module>')
@decorators.jsonify('module')
def get_module(module):
    module_id_index = vault.get_vault()['module_id_index']
    module = module.lower()
    if module in module_id_index:
        return module_id_index[module]
    flask.abort(404)


@app.route('/api/1.0/stats/bp')
@decorators.jsonify('stats')
@decorators.exception_handler()
@decorators.record_filter()
def get_bpd(records):
    result = []
    for record in records:
        if record['record_type'] in ['bpd', 'bpc']:
            mention_date = record.get('mention_date')
            if mention_date:
                date = helpers.format_date(mention_date)
            else:
                date = 'never'
            result.append({
                'date': date,
                'status': record['lifecycle_status'],
                'metric': record.get('mention_count') or 0,
                'id': record['name'],
                'name': record['name'],
                'link': helpers.make_blueprint_link(record['module'],
                                                    record['name'])
            })

    result.sort(key=lambda x: x['metric'], reverse=True)

    return result


@app.route('/api/1.0/users')
@decorators.jsonify('users')
@decorators.exception_handler()
@decorators.record_filter(ignore='user_id')
def get_users_json(records):
    user_name_query = flask.request.args.get('user_name') or ''
    user_ids = set()
    result = []
    for record in records:
        user_id = record['user_id']
        if user_id in user_ids:
            continue
        user_name = record['author_name']
        if user_name.lower().find(user_name_query.lower()) >= 0:
            user_ids.add(user_id)
            result.append({'id': user_id, 'text': user_name})
    result.sort(key=lambda x: x['text'])
    return result


@app.route('/api/1.0/users/<user_id>')
@decorators.jsonify('user')
def get_user(user_id):
    user = vault.get_user_from_runtime_storage(user_id)
    if not user:
        flask.abort(404)
    user = helpers.extend_user(user)
    return user


@app.route('/api/1.0/releases')
@decorators.jsonify('releases')
@decorators.exception_handler()
def get_releases_json():
    query = (flask.request.args.get('query') or '').lower()
    return [{'id': r['release_name'], 'text': r['release_name'].capitalize()}
            for r in vault.get_release_options()
            if r['release_name'].find(query) >= 0]


@app.route('/api/1.0/releases/<release>')
@decorators.jsonify('release')
def get_release_json(release):
    if release != 'all':
        if release not in vault.get_vault()['releases']:
            release = parameters.get_default('release')

    return {'id': release, 'text': release.capitalize()}


@app.route('/api/1.0/metrics')
@decorators.jsonify('metrics')
@decorators.exception_handler()
def get_metrics_json():
    query = (flask.request.args.get('query') or '').lower()
    return sorted([{'id': m, 'text': t}
                   for m, t in parameters.METRIC_LABELS.iteritems()
                   if t.lower().find(query) >= 0],
                  key=operator.itemgetter('text'))


@app.route('/api/1.0/metrics/<metric>')
@decorators.jsonify('metric')
@decorators.exception_handler()
def get_metric_json(metric):
    if metric not in parameters.METRIC_LABELS:
        metric = parameters.get_default('metric')
    return {'id': metric, 'text': parameters.METRIC_LABELS[metric]}


@app.route('/api/1.0/project_types')
@decorators.jsonify('project_types')
@decorators.exception_handler()
def get_project_types_json():
    return [{'id': m, 'text': m, 'items': list(t)}
            for m, t in vault.get_project_type_options().iteritems()]


@app.route('/api/1.0/project_types/<project_type>')
@decorators.jsonify('project_type')
@decorators.exception_handler()
def get_project_type_json(project_type):
    if project_type != 'all':
        for pt, groups in vault.get_project_type_options().iteritems():
            if (project_type == pt) or (project_type in groups):
                break
        else:
            project_type = parameters.get_default('project_type')

    return {'id': project_type, 'text': project_type}


@app.route('/api/1.0/stats/timeline')
@decorators.jsonify('timeline')
@decorators.exception_handler()
@decorators.record_filter(ignore='release')
def timeline(records, **kwargs):
    # find start and end dates
    release_names = parameters.get_parameter(kwargs, 'release', 'releases')
    releases = vault.get_vault()['releases']
    if not release_names:
        flask.abort(404)

    if 'all' in release_names:
        start_date = release_start_date = utils.timestamp_to_week(
            vault.get_vault()['start_date'])
        end_date = release_end_date = utils.timestamp_to_week(
            vault.get_vault()['end_date'])
    else:
        release = releases[release_names[0]]
        start_date = release_start_date = utils.timestamp_to_week(
            release['start_date'])
        end_date = release_end_date = utils.timestamp_to_week(
            release['end_date'])

    now = utils.timestamp_to_week(int(time.time())) + 1

    # expand start-end to year if needed
    if release_end_date - release_start_date < 52:
        expansion = (52 - (release_end_date - release_start_date)) // 2
        if release_end_date + expansion < now:
            end_date += expansion
        else:
            end_date = now
        start_date = end_date - 52

    # empty stats for all weeks in range
    weeks = range(start_date, end_date)
    week_stat_loc = dict((c, 0) for c in weeks)
    week_stat_commits = dict((c, 0) for c in weeks)
    week_stat_commits_hl = dict((c, 0) for c in weeks)

    param = parameters.get_parameter(kwargs, 'metric')
    if ('commits' in param) or ('loc' in param):
        handler = lambda record: record['loc']
    else:
        handler = lambda record: 0

    # fill stats with the data
    for record in records:
        week = record['week']
        if week in weeks:
            week_stat_loc[week] += handler(record)
            week_stat_commits[week] += 1
            if 'all' in release_names or record['release'] in release_names:
                week_stat_commits_hl[week] += 1

    # form arrays in format acceptable to timeline plugin
    array_loc = []
    array_commits = []
    array_commits_hl = []

    for week in weeks:
        week_str = utils.week_to_date(week)
        array_loc.append([week_str, week_stat_loc[week]])
        array_commits.append([week_str, week_stat_commits[week]])
        array_commits_hl.append([week_str, week_stat_commits_hl[week]])

    return [array_commits, array_commits_hl, array_loc]


gravatar = gravatar_ext.Gravatar(app, size=64, rating='g', default='wavatar')


def main():
    app.run(cfg.CONF.listen_host, cfg.CONF.listen_port)

if __name__ == '__main__':
    main()
