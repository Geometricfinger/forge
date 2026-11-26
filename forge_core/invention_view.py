"""Escaped presentation of retained, declaration-based invention options.

Validation and synthesis belong to opportunities and invention_options. This
module renders their result without following citations or executing a route.
"""
import html


def _escape(value):
    return html.escape(str(value), quote=True)


def _label(value):
    return str(value).replace('_', ' ').lower().capitalize()


def _items(values):
    return '<ul>' + ''.join('<li>' + _escape(value) + '</li>' for value in values) + '</ul>'


def _value(value):
    if value is None:
        return 'Not assessed'
    if type(value) is bool:
        return 'Yes' if value else 'No'
    return str(value)


def _evidence(refs):
    if not refs:
        return '<p><small>No condition declaration retained.</small></p>'
    return '<p><small>Evidence: ' + '; '.join(
        _escape(ref['source']) + ' [' + _escape(ref['start']) + ', '
        + _escape(ref['end']) + ')' for ref in refs) + '</small></p>'


def _conditions(rows):
    parts = []
    for row in rows:
        parts.append('<li><b>' + _escape(row['condition']) + '</b>: '
                     + _escape(_label(row['status'])) + _evidence(row['evidence']) + '</li>')
    return '<ul>' + ''.join(parts) + '</ul>'


def render_build_options(hypothesis):
    """Return a review section, or no section for a historical saved report."""
    plan = hypothesis.get('build_options')
    if plan is None:
        return ''
    parts = ['<section><h3>Build options for review</h3>',
             '<p><b>' + _escape(_label(plan['status'])) + '</b></p>',
             '<p>Required output: <code>' + _escape(plan['target_field']) + '</code></p>',
             '<p>These routes combine reviewed declarations. Different mechanism sets do not '
             'establish different capabilities, tested behavior or novelty.</p>']
    if plan.get('next_decision'):
        parts.append('<p><b>Next decision:</b> ' + _escape(_label(plan['next_decision'])) + '</p>')
    if plan['required_conditions']:
        parts.append('<p><b>Required conditions:</b> '
                     + _escape(', '.join(plan['required_conditions'])) + '</p>')

    notices = {
        'EXISTING_OPTION_REVIEW_REQUIRED': 'Review the cited existing option for this exact task before proposing new work.',
        'EVIDENCE_REVIEW_REQUIRED': 'Resolve the supporting, contrary or stale evidence before generating build routes.',
        'NO_NEW_WORK_DECLARED_OUTPUT_AVAILABLE': 'The required output is already available in the declared workflow. No new build follows from this map.',
        'NO_DECLARED_ROUTE': 'No complete route follows from these declarations. Review missing inputs and conditions; an absent route does not establish that no external solution exists.',
        'NO_PLAN_FOUND_WITHIN_BOUNDS': 'No complete route was retained within the search bounds. Review the limits and unresolved inputs before drawing a conclusion.',
    }
    if plan['status'] in notices:
        parts.append('<p>' + notices[plan['status']] + '</p>')
    if plan['options']:
        parts.append('<p><small>Review order considers unconfirmed conditions, new work and route length. '
                     'It is not a usefulness, cost or success ranking.</small></p>')
    for number, option in enumerate(plan['options'], 1):
        parts.append('<details><summary>Option ' + str(number) + ' · '
                     + _escape(_label(option['classification'])) + ' · '
                     + str(len(option['steps'])) + ' steps'
                     + (' · conditions unconfirmed' if option['unconfirmed_conditions'] else '')
                     + '</summary>')
        parts.append('<p><small>' + _escape(option['id']) + '</small></p>')
        parts.append('<p><b>Operating conditions:</b> '
                     + _escape(_label(option['condition_status'])) + '</p>')
        if option['unconfirmed_conditions']:
            parts.append('<p><b>Conditions still unknown</b></p>' + _items(
                row['mechanism'] + ': ' + row['condition'] for row in option['unconfirmed_conditions']))
        if option['unimplemented_mechanisms']:
            parts.append('<p><b>New work requiring implementation and evidence</b></p>'
                         + _items(option['unimplemented_mechanisms']))
        else:
            parts.append('<p><small>No step is declared as new work; the complete route still needs validation.</small></p>')
        parts.append('<ol>')
        for step in option['steps']:
            parts.append('<li><h4>' + _escape(step['title']) + '</h4><p>'
                         + _escape(_label(step['role'])) + ' · ' + _escape(step['mechanism']) + '</p>'
                         + '<p>' + _escape(step['description']) + '</p>')
            parts.append('<p><b>Requires:</b> ' + _escape(', '.join(step['requires']) or 'No declared input')
                         + '<br><b>Provides:</b> ' + _escape(', '.join(step['provides']) or 'No declared output') + '</p>')
            if step['input_bindings']:
                parts.append('<p><b>Input sources</b></p>' + _items(
                    row['field'] + ' ← ' + ('workflow input' if row['origin'] == 'workflow'
                                           else 'step ' + str(row['mechanism']))
                    for row in step['input_bindings']))
            if step['condition_checks']:
                parts.append('<p><b>Condition declarations</b></p>' + _conditions(step['condition_checks']))
            parts.append('<p><b>Limits</b></p>' + _items(step['limits']))
            parts.append(_evidence(step['evidence']))
            if step['code_queries']:
                parts.append('<p><b>Source queries:</b> ' + _escape('; '.join(step['code_queries'])) + '</p>')
            parts.append('</li>')
        parts.append('</ol><p><small>Mechanisms were not executed by this plan. Review source rights, '
                     'adapters and acceptance cases before a separate authorized experiment.</small></p></details>')

    unresolved = plan['unresolved']
    if any(unresolved.values()):
        parts.append('<details><summary>Unresolved inputs and excluded routes</summary>')
        parts.append('<p>These catalog branches may be unused by the retained options. '
                     'Check each option\'s own input bindings and conditions.</p>')
        if unresolved['missing_inputs_without_provider']:
            parts.append('<h4>Inputs with no declared provider</h4>'
                         + _items(unresolved['missing_inputs_without_provider']))
        if unresolved['unreachable_mechanisms']:
            parts.append('<h4>Unreachable mechanisms</h4>' + _items(
                row['mechanism'] + ': requires ' + ', '.join(row['missing_inputs'])
                for row in unresolved['unreachable_mechanisms']))
        if unresolved['cyclic_dependency_groups']:
            parts.append('<h4>Unseeded dependency cycles</h4>'
                         + _items(', '.join(group) for group in unresolved['cyclic_dependency_groups']))
        if unresolved['incompatible_mechanisms']:
            parts.append('<h4>Excluded by a required condition</h4>')
            for row in unresolved['incompatible_mechanisms']:
                parts.append('<p><b>' + _escape(row['mechanism']) + '</b></p>' + _conditions(row['conditions']))
        parts.append('<p><small>These diagnostics concern the supplied map. They are not a minimal repair '
                     'plan or proof that all alternatives were searched.</small></p></details>')

    coverage = plan['coverage']
    columns = [
        ('Search performed', 'search_performed'),
        ('Mechanisms supplied', 'mechanisms_supplied'), ('Relevant mechanisms', 'relevant_mechanisms'),
        ('Eligible mechanisms', 'eligible_mechanisms'), ('Maximum steps per route', 'max_steps'),
        ('Maximum retained states', 'max_states'), ('Maximum displayed options', 'max_options'),
        ('States retained', 'states_retained'), ('States expanded', 'states_expanded'),
        ('Minimal complete sets found', 'minimal_plans_found'), ('Options retained', 'options_retained'),
        ('Options omitted', 'options_omitted'),
        ('Transitions omitted by state limit', 'transitions_omitted_by_state_budget'),
        ('Transitions omitted by depth limit', 'transitions_omitted_by_depth'),
        ('Equivalent orders collapsed', 'equivalent_orders_collapsed'),
        ('Redundant complete sets omitted', 'redundant_complete_sets_omitted'),
        ('State budget exhausted', 'state_budget_exhausted'), ('Depth limited', 'depth_limited'),
        ('Search complete within depth bound', 'search_complete_within_depth'),
        ('Declared reachable without depth limit', 'declared_reachable_without_depth_limit'),
    ]
    parts.append('<details><summary>Search coverage and limits</summary><dl>')
    for title, field in columns:
        if field in coverage:
            parts.append('<dt>' + title + '</dt><dd>' + _escape(_value(coverage[field])) + '</dd>')
    parts.append('</dl><p>Selection: ' + _escape(_label(coverage['selection_policy'])) + '</p>')
    parts.append('<p>Inclusion minimality concerns mechanism sets for one declared output. It does not '
                 'establish minimum effort, cost, operator intervention or market value.</p></details>')
    if plan['limitations']:
        parts.append('<details><summary>Interpretation limits</summary>' + _items(plan['limitations']) + '</details>')
    parts.append('</section>')
    return ''.join(parts)
