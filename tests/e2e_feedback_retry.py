"""Feedback retry IDs and late-response isolation against the real Vue UI.

The simulated server can save a feedback and lose its acknowledgement.
"""
import argparse
import json
from datetime import datetime
from playwright.sync_api import expect, sync_playwright
from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures


def check(browser, output, scenario):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    install_api_fixtures(page)
    calls, saved, pending, errors = [], {}, [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def feedback(route):
        payload = route.request.post_data_json
        calls.append(payload)
        saved[payload['feedback_id']] = payload
        if len(calls) == 1:
            if scenario.startswith('late-'): pending.append(route)
            else: route.fulfill(status=409 if scenario == 'conflict' else 502,
                json={'detail': 'Synthetic conflict' if scenario == 'conflict' else 'Synthetic acknowledgement lost'})
        else:
            route.fulfill(json={'feedback_id': payload['feedback_id'], 'status': 'recorded'})

    page.route('**/api/chat/feedback', feedback)
    try:
        page.goto(BASE_URL + '/chat', wait_until='domcontentloaded')
        page.locator('#question').fill('差旅报销需要哪些审批？')
        page.get_by_role('button', name='发送', exact=True).click()
        trigger = page.get_by_role('button', name='反馈回答', exact=True).last
        expect(trigger).to_be_visible()
        trigger.click()
        dialog = page.get_by_role('dialog', name='反馈回答', exact=True)
        note = dialog.get_by_placeholder('补充说明（可选）')
        note.fill('请补充审批条件')
        dialog.get_by_role('button', name='提交反馈', exact=True).click()
        if scenario.startswith('late-'):
            expect(dialog.get_by_role('button', name='提交中…')).to_be_disabled()
            dialog.get_by_role('button', name='取消', exact=True).click()
            expect(dialog).to_have_count(0)
            trigger.click()
            note.fill('另一条补充意见')
            assert pending
            if scenario == 'late-error': pending[0].fulfill(status=502, json={'detail': 'Synthetic late error'})
            else: pending[0].fulfill(json={'feedback_id': calls[0]['feedback_id'], 'status': 'recorded'})
            expect(dialog.get_by_role('button', name='提交反馈', exact=True)).to_be_enabled()
            expect(note).to_have_value('另一条补充意见')
            expect(dialog.get_by_role('alert')).to_have_count(0)
        else:
            expect(dialog.get_by_role('alert')).to_contain_text('冲突' if scenario == 'conflict' else '反馈提交失败')
            expect(note).to_have_value('请补充审批条件')
            if scenario in ('edited', 'conflict'):
                note.fill('引用没有覆盖审批条件')
                dialog.get_by_role('radio', name='引用与回答不匹配', exact=True).check()
        dialog.get_by_role('button', name='提交反馈', exact=True).click()
        expect(dialog).to_have_count(0)
        assert len(calls) == 2, calls
        if scenario == 'unchanged':
            assert calls[0] == calls[1], calls
            assert len(saved) == 1, saved
        else:
            assert calls[0]['feedback_id'] != calls[1]['feedback_id'], calls
            assert calls[1]['note'] == ('另一条补充意见' if scenario.startswith('late-') else '引用没有覆盖审批条件'), calls
            if scenario in ('edited', 'conflict'): assert calls[1]['category'] == 'citation_mismatch', calls
        assert not errors, errors
        page.screenshot(path=str(output / f'{scenario}.png'))
        return {'calls': calls, 'saved_count': len(saved), 'page_errors': errors}
    except Exception:
        page.screenshot(path=str(output / f'failed-{scenario}.png'))
        (output / f'failed-{scenario}.json').write_text(json.dumps({'calls': calls, 'page_errors': errors},
            ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browsers', nargs='+', default=['chrome'], choices=['chrome', 'msedge', 'firefox', 'webkit'])
    parser.add_argument('--scenario', action='append', choices=['unchanged', 'edited', 'conflict', 'late-response', 'late-error'])
    options = parser.parse_args()
    output = ROOT / 'work' / 'feedback-retry' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report = {'mocked_api': True, 'checks': []}
    with sync_playwright() as p:
        for name in options.browsers:
            browser = p.chromium.launch(channel=name) if name in ('chrome', 'msedge') else getattr(p, name).launch()
            browser_output = output / name
            browser_output.mkdir()
            try:
                for scenario in options.scenario or ['unchanged', 'edited', 'conflict', 'late-response', 'late-error']:
                    row = {'browser': name, 'version': browser.version, 'scenario': scenario, 'status': 'passed'}
                    try: row['detail'] = check(browser, browser_output, scenario)
                    except Exception as error: row.update(status='failed', error=str(error))
                    report['checks'].append(row)
                    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(name, scenario, row['status'], flush=True)
            finally:
                browser.close()
    passed = sum(row['status'] == 'passed' for row in report['checks'])
    print(f'Feedback retry {passed}/{len(report["checks"])} passed. Evidence: {output}', flush=True)
    return int(passed != len(report['checks']))


if __name__ == '__main__': raise SystemExit(main())
