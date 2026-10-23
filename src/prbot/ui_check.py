"""Remote CI only: exercise the packaged WebKit UI and export a real window snapshot."""

import base64
import json
import os
from pathlib import Path


def schedule_check(web, output):
    import AppKit
    import WebKit
    from PyObjCTools import AppHelper

    # The ui-check entry point has already selected a disposable HOME.
    auth_path = Path.home() / ".codex/auth.json"
    auth_path.parent.mkdir(mode=0o700)

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    fixture_token = encode({"alg": "RS256"}) + "." + encode({"sub": "ui-test"}) + ".c2ln"
    original_auth = json.dumps(
        {"tokens": {"id_token": fixture_token, "access_token": "ui-access-only"}}
    )
    auth_path.write_text(original_auth)
    auth_path.chmod(0o600)
    finished = False
    interaction_error = None
    minimum = False

    def finish(ok, detail):
        nonlocal finished
        if finished:
            return
        if auth_path.read_text() != original_auth:
            ok, detail = False, "Fetching or applying the ID token modified native credentials"
        finished = True
        output.with_suffix(".json").write_text(json.dumps({"ok": ok, "detail": detail}))
        # AppHelper.stopEventLoop may terminate NSApplication with exit status 0.
        # This isolated CI subprocess must propagate its actual verdict to Actions.
        os._exit(0 if ok else 2)

    def snapshot(image, error):
        if error or image is None:
            finish(False, "WebKit snapshot failed")
            return
        bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        destination = output.with_name(output.stem + "-minimum.png") if minimum else output
        ok = bool(data.writeToFile_atomically_(str(destination), True))
        if ok and interaction_error is None and not minimum:
            check_minimum()
            return
        finish(
            ok and interaction_error is None,
            interaction_error or "Native bridge, drafts, settings, focus and two sizes checked",
        )

    def check_minimum():
        nonlocal minimum
        minimum = True
        web.window().setContentSize_((760, 580))
        AppHelper.callLater(0.25, inspect_minimum)

    def inspect_minimum():
        web.evaluateJavaScript_completionHandler_(
            """
(async () => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  document.getElementById('user').click();
  await new Promise(resolve => setTimeout(resolve, 80));
  const bounds = document.getElementById('settings').getBoundingClientRect();
  assert(bounds.left >= 15 && bounds.right <= innerWidth - 15, 'Minimum settings width');
  assert(bounds.top >= 15 && bounds.bottom <= innerHeight - 15, 'Minimum settings height');
  for (const button of document.querySelectorAll('.workspace-row button')) {
    const rect = button.getBoundingClientRect();
    assert(rect.right < bounds.right && rect.bottom < bounds.bottom, 'Clipped workspace button');
  }
  const close = document.getElementById('close-settings').getBoundingClientRect();
  assert(close.top >= 0 && close.right < innerWidth, 'Close settings button clipped');
  const configuration = document.querySelector('[data-section="Configuration"]');
  for (let count = 0; configuration.disabled && count < 300; count++) {
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  configuration.click();
  for (let count = 0; count < 300; count++) {
    if (document.getElementById('INFISICAL_URL')
      && !document.getElementById('INFISICAL_URL').disabled) break;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  const content = document.querySelector('.settings-content');
  assert(content.scrollHeight > content.clientHeight, 'Configuration should scroll');
  for (const input of document.querySelectorAll('input[name^="INFISICAL_"]')) {
    input.scrollIntoView({block:'center'});
    const rect = input.getBoundingClientRect();
    const area = content.getBoundingClientRect();
    assert(rect.left >= area.left && rect.right <= area.right, 'Configuration field clipped');
    assert(rect.top >= area.top && rect.bottom <= area.bottom, 'Configuration cannot scroll');
  }
  document.getElementById('save-configuration').scrollIntoView({block:'center'});
  assert(document.getElementById('save-configuration').getBoundingClientRect().bottom
    <= content.getBoundingClientRect().bottom, 'Save configuration cannot scroll');
  content.scrollTop = 0;
  window.__uiCheckResult = 'ok';
})().catch(error => { window.__uiCheckResult = 'failed:' + error.message; });
true;
""",
            lambda result, error: poll_result(),
        )

    def checked(result, error):
        nonlocal interaction_error
        if finished:
            return
        if error or (isinstance(result, str) and result.startswith("failed:")):
            interaction_error = result if not error else "JavaScript evaluation failed"
        if interaction_error or result == "ok":
            web.takeSnapshotWithConfiguration_completionHandler_(
                WebKit.WKSnapshotConfiguration.alloc().init(), snapshot
            )
        else:
            AppHelper.callLater(0.1, poll_result)

    def poll_result():
        web.evaluateJavaScript_completionHandler_("window.__uiCheckResult || 'pending'", checked)

    def poll():
        if finished:
            return
        web.evaluateJavaScript_completionHandler_(
            "document.readyState === 'complete' && typeof window.receiveNative === 'function'"
            " && document.getElementById('service-label')"
            " && document.getElementById('service-label').textContent !== 'Unknown'",
            loaded,
        )

    def loaded(result, error):
        if not result or error:
            AppHelper.callLater(0.25, poll)
            return
        # Native ready/status bridge has completed. Await React's updates after user events.
        web.evaluateJavaScript_completionHandler_(
            """
(async () => {
  const assert = (value, label) => { if (!value) throw new Error(label || 'UI assertion failed'); };
  const tick = () => new Promise(resolve => setTimeout(resolve, 40));
  assert(getComputedStyle(document.querySelector('#root > div')).display === 'flex',
    'Tailwind root flex layout missing');
  assert(document.querySelector('aside').getBoundingClientRect().width === 252,
    'Sidebar width is not 252px');
  assert(getComputedStyle(document.querySelector('#composer')).borderRadius === '17px',
    'Composer border radius missing');
  assert(document.querySelectorAll('[data-view]').length === 2);
  assert(document.getElementById('send').disabled, 'Invalid empty submission is enabled');
  const input = document.getElementById('draft');
  const edit = (element, value) => {
    const prototype = element.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event('input', {bubbles:true}));
  };
  edit(document.getElementById('target-link'), 'https://github.com/owner/repo');
  edit(input, 'Create documentation and explain acceptance criteria');
  await tick();
  assert(!document.getElementById('send').disabled);
  document.querySelector('[data-view="Pull Request"]').click();
  await tick();
  assert(document.getElementById('draft').value === '', 'Mode drafts are mixed');
  assert(document.getElementById('send').disabled);
  document.querySelector('[data-view="Issue"]').click();
  await tick();
  assert(document.getElementById('draft').value.includes('documentation'));
  // Hold bridge responses to reproduce polling, double-click and heartbeat races.
  const field = id => document.getElementById(id);
  const waitFor = async predicate => {
    for (let count = 0; count < 300; count++) { if (predicate()) return; await tick(); }
    throw new Error('Native response timed out');
  };
  await waitFor(() => !field('send').disabled);
  const handler = window.webkit.messageHandlers.prbot;
  const originalPost = handler.postMessage;
  const requests = [];
  handler.postMessage = body => { requests.push(body); };
  const complete = action => window.receiveNative({ok:true, busy:false, completed_action:action});
  try {
    for (let length = 1; length <= 7; length++) {
      edit(field('draft'), 'x'.repeat(length)); await tick();
      assert(!field('send').disabled, 'Short description rejected');
    }
    edit(field('draft'), '   '); await tick();
    assert(field('send').disabled, 'Whitespace description accepted');
    edit(field('draft'), 'fix'); await tick();
    const labels = [];
    const observer = new MutationObserver(() => labels.push(field('send').textContent));
    observer.observe(field('send'), {subtree:true, childList:true, characterData:true});
    await waitFor(() => requests.some(request => request.action === 'tasks'));
    assert(field('send').textContent === 'Send', 'Polling changed Send label');
    complete('tasks'); await tick();
    assert(!labels.includes('Submitting…'), 'Polling flickered submission label');
    observer.disconnect();
    field('composer').requestSubmit(); field('composer').requestSubmit(); await tick();
    assert(requests.filter(request => request.action === 'submit_message').length === 1,
      'Double click submitted twice');
    assert(field('send').textContent === 'Submitting…', 'Missing submit progress');
    window.receiveNative({ok:true, busy:false, status:{state:'stopped'}}); await tick();
    assert(field('send').textContent === 'Submitting…', 'Heartbeat released submit lock');
    complete('tasks'); await tick();
    assert(field('send').textContent === 'Submitting…', 'Other response released submit lock');
    window.receiveNative({ok:false, busy:false, completed_action:'submit_message',
      message:'Message not saved. Retry.'}); await tick();
    assert(field('send').textContent === 'Send' && !field('send').disabled);
    assert(field('draft').value === 'fix', 'Failed submit lost draft');
    field('composer').requestSubmit(); await tick();
    const submitted = requests.filter(request => request.action === 'submit_message');
    assert(submitted.length === 2 && submitted[0].request_id === submitted[1].request_id,
      'Retry lost idempotency key');
    const report = JSON.stringify({summary:'Implemented', outcome:'delivered_unverified',
      pr_url:'https://github.com/owner/repo/pull/1', validation:'Required remote checks failed.',
      delivery:{code:'completed_by_codex',pr:'open',issue:'closed',ci:'failed'}});
    const task = {id:submitted[1].request_id,mode:'Issue',status:'delivered_unverified',
      conversation_id:'ui',created_at:1,result:report};
    window.receiveNative({ok:true,busy:false,completed_action:'submit_message',accepted:task,tasks:[task]});
    await tick();
    assert(field('draft').value === '', 'Accepted submission did not clear draft');
    assert(document.querySelector('[aria-label="Task status"]').textContent.includes(
      'Delivered · verification incomplete'), 'Delivery is hidden by generic failure');
    const facts = document.querySelector('[aria-label="Delivery facts"]').textContent;
    assert(facts.includes('open') && facts.includes('closed') && facts.includes('failed'),
      'Separate delivery facts missing');
    complete('history'); await tick();
  } finally { handler.postMessage = originalPost; }
  edit(field('draft'), 'Create documentation and explain acceptance criteria'); await tick();
  document.getElementById('user').click();
  await tick();
  assert(document.getElementById('settings').open);
  assert(document.querySelectorAll('[data-section]').length === 4);
  assert(document.querySelectorAll('.workspace-row').length === 2);
  const buttons = Array.from(document.querySelectorAll('.workspace-row button'));
  assert(buttons[0].getBoundingClientRect().width === buttons[1].getBoundingClientRect().width);
  assert(buttons[0].getBoundingClientRect().right === buttons[1].getBoundingClientRect().right);
  const select = async name => {
    const button = document.querySelector('[data-section="' + name + '"]');
    await waitFor(() => !button.disabled); button.click(); await tick();
  };
  await select('Connections');
  const fetchToken = field('fetch-codex-id-token');
  await waitFor(() => !fetchToken.disabled); fetchToken.click(); await tick();
  // The bridge unlocks controls before SettingsPanel's effect renders the token.
  // Wait for the visible result as well, rather than racing that second render.
  await waitFor(() => !fetchToken.disabled && field('id-token').value.startsWith('eyJ'));
  assert(field('id-token').value.startsWith('eyJ'), 'Fetch did not fill ID token');
  const fetched = field('id-token').value;
  edit(field('id-token'), 'ui-manual-id'); await tick();
  window.receiveNative({ok:false, message:'Cannot read Codex credentials. Check permissions.'});
  await tick();
  assert(field('id-token').value === 'ui-manual-id', 'Fetch failure lost ID draft');
  const useToken = () => Array.from(document.querySelectorAll('button'))
    .find(b => b.textContent === 'Use token');
  useToken().click(); await tick();
  await waitFor(() => !fetchToken.disabled && useToken().disabled);
  assert(field('id-token').value === 'ui-manual-id', 'Manual apply lost ID token');
  assert(useToken().disabled, 'Applied ID token still dirty');
  fetchToken.click(); await tick();
  await waitFor(() => !fetchToken.disabled && field('id-token').value === fetched);
  assert(field('id-token').value === fetched, 'Fetch returned manual override');
  useToken().click(); await tick();
  await waitFor(() => !fetchToken.disabled && useToken().disabled);
  await select('Configuration');
  await waitFor(() => field('INFISICAL_URL') && !field('INFISICAL_URL').disabled);
  const examples = {INFISICAL_URL:'https://app.infisical.com',
    INFISICAL_CLIENT_ID:'your-client-id', INFISICAL_CLIENT_SECRET:'your-client-secret',
    INFISICAL_PROJECT_ID:'your-project-id', INFISICAL_ENVIRONMENT:'dev',
    INFISICAL_SECRET_PATH:'/ or /prbot'};
  const checkFieldHelp = () => {
    for (const [key,example] of Object.entries(examples)) {
      const element = field(key);
      const label = document.querySelector('label[for="' + key + '"]');
      assert(label.textContent.includes(key), 'Missing key label: ' + key);
      assert(label.textContent.trim() !== key, 'Missing friendly name: ' + key);
      assert(element.name === key && !element.disabled, 'Field mapping/editability: ' + key);
      const help = field(element.getAttribute('aria-describedby'));
      assert(help && help.textContent.includes('Example: ' + example), 'Missing example: ' + key);
      assert(getComputedStyle(help).display !== 'none' && help.getClientRects().length,
        'Hidden help: ' + key);
    }
    assert(field('INFISICAL_CLIENT_SECRET').type === 'password', 'Secret is visible');
  };
  const assertValues = (values, saved = false) => {
    for (const [key,value] of Object.entries(values)) {
      assert(field(key).value === (saved && key === 'INFISICAL_CLIENT_SECRET' ? '' : value),
        'Configuration value mismatch: ' + key);
    }
  };
  const editValues = async values => {
    for (const [key,value] of Object.entries(values)) { edit(field(key), value); await tick(); }
  };
  checkFieldHelp();
  assert(field('INFISICAL_CLIENT_ID').value === '', 'Client ID example became input');
  assert(field('INFISICAL_PROJECT_ID').value === '', 'Project ID example became input');
  assert(field('INFISICAL_CLIENT_SECRET').value === '', 'Secret example became input');
  const values = {INFISICAL_URL:'http://127.0.0.1:1', INFISICAL_CLIENT_ID:'ui-client',
    INFISICAL_CLIENT_SECRET:'ui-secret-only', INFISICAL_PROJECT_ID:'ui-project',
    INFISICAL_ENVIRONMENT:'staging', INFISICAL_SECRET_PATH:'/prbot'};
  await editValues(values);
  checkFieldHelp(); assertValues(values);
  await select('Workspace'); await select('Configuration');
  assertValues(values);
  // Background status and task refreshes must not replace unsaved edits.
  window.receiveNative({ok:true, status:{state:'stopped',loaded:false}, tasks:[]});
  await tick(); assertValues(values);
  const restorePost = handler.postMessage;
  handler.postMessage = body => {
    if (body.action === 'save_configuration') {
      window.__configurationRequest = body;
    } else restorePost.call(handler, body);
  };
  try {
    field('save-configuration').click(); await tick();
    assertValues(window.__configurationRequest.configuration);
    for (const [key,value] of Object.entries(values)) {
      assert(window.__configurationRequest.configuration[key] === value,
        'Submitted field mismatch: ' + key);
    }
    window.receiveNative({ok:false, busy:false, completed_action:'save_configuration',
      message:'Cannot save configuration. Check write permissions.'});
    await tick(); assertValues(values);
    assert(field('settings-feedback').textContent.includes('write permissions'),
      'Missing save failure reason');
  } finally { handler.postMessage = restorePost; }
  edit(field('INFISICAL_SECRET_PATH'), 'relative'); await tick();
  field('save-configuration').click(); await tick();
  await waitFor(() => !field('INFISICAL_URL').disabled
    && field('settings-feedback').textContent.includes('must start with /'));
  assertValues({...values, INFISICAL_SECRET_PATH:'relative'});
  edit(field('INFISICAL_SECRET_PATH'), values.INFISICAL_SECRET_PATH); await tick();
  field('save-configuration').click(); await tick();
  await waitFor(() => !field('INFISICAL_URL').disabled
    && field('save-configuration').disabled
    && field('INFISICAL_CLIENT_SECRET').value === '');
  assertValues(values, true); checkFieldHelp();
  assert(field('settings-feedback').textContent.includes('Restart PRBot'), 'Missing apply scope');
  assert(field('INFISICAL_CLIENT_SECRET').placeholder.includes('Configured'),
    'Missing secret state');
  assert(field('INFISICAL_CLIENT_SECRET').parentElement.textContent.includes('Leave blank'),
    'Missing secret retention help');
  const updated = {...values, INFISICAL_URL:'http://127.0.0.1:2',
    INFISICAL_CLIENT_ID:'ui-client-updated', INFISICAL_CLIENT_SECRET:'',
    INFISICAL_PROJECT_ID:'ui-project-updated', INFISICAL_ENVIRONMENT:'production',
    INFISICAL_SECRET_PATH:'/updated'};
  await editValues(updated);
  field('save-configuration').click(); await tick();
  await waitFor(() => !field('INFISICAL_URL').disabled && field('save-configuration').disabled);
  await select('Workspace'); await select('Configuration');
  await waitFor(() => !field('INFISICAL_URL').disabled);
  assertValues(updated, true); checkFieldHelp();
  const reload = Array.from(document.querySelectorAll('button'))
    .find(button => button.textContent === 'Reload configuration');
  reload.click(); await tick();
  await waitFor(() => !field('INFISICAL_URL').disabled);
  assertValues(updated, true); checkFieldHelp();
  assert(field('INFISICAL_CLIENT_SECRET').placeholder.includes('Configured'),
    'Blank save lost configured Secret');
  await select('Background Service');
  await tick();
  assert(document.querySelectorAll('#service-power').length === 1);
  assert(document.querySelector('.service-core'));
  window.receiveNative({ok:true, status:{state:'stale',loaded:true}});
  await tick();
  assert(document.getElementById('service-power').getAttribute('aria-label') === 'Refresh status');
  document.querySelector('[data-section="Workspace"]').click();
  await tick();
  document.getElementById('close-settings').click();
  await tick();
  assert(!document.getElementById('settings').open);
  assert(document.activeElement.id === 'user', 'Dialog did not restore focus');
  assert(!/[\u4e00-\u9fff]/.test(document.body.innerText), 'Non-English product copy');
  window.__uiCheckResult = 'ok';
})().catch(error => { window.__uiCheckResult = 'failed:' + error.message; });
true;
""",
            lambda result, error: poll_result(),
        )

    AppHelper.callLater(0.25, poll)
    AppHelper.callLater(45, lambda: finish(False, "UI load or snapshot timed out"))
