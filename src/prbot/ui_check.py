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
window.__uiCheckResult = 'pending';
(async () => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  document.getElementById('user').click();
  await new Promise(resolve => setTimeout(resolve, 80));
  const bounds = document.getElementById('settings').getBoundingClientRect();
  assert(bounds.left >= 15 && bounds.right <= innerWidth - 15,
    `Minimum settings width: left=${bounds.left} right=${bounds.right} viewport=${innerWidth}`);
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
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
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

    def paste_request(value, error):
        if finished:
            return
        if not error and value:
            # Exercise the actual native Cmd+V menu equivalent and WebKit responder chain.
            board = AppKit.NSPasteboard.generalPasteboard()
            board.clearContents()
            board.setString_forType_("native-paste-fixture", AppKit.NSPasteboardTypeString)
            key_event = getattr(
                AppKit.NSEvent,
                "keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_"
                "characters_charactersIgnoringModifiers_isARepeat_keyCode_",
            )
            event = key_event(
                AppKit.NSEventTypeKeyDown,
                (0, 0),
                AppKit.NSEventModifierFlagCommand,
                0,
                web.window().windowNumber(),
                None,
                "v",
                "v",
                False,
                9,
            )
            handled = (
                AppKit.NSApplication.sharedApplication().mainMenu().performKeyEquivalent_(event)
            )
            web.evaluateJavaScript_completionHandler_(
                "window.__pasteRequest = false; window.__pasteHandled = "
                + ("true" if handled else "false"),
                None,
            )
        AppHelper.callLater(0.05, paste_poll)

    def paste_poll():
        if not finished:
            web.evaluateJavaScript_completionHandler_("!!window.__pasteRequest", paste_request)

    def wheel_request(value, error):
        if finished:
            return
        if not error and value:
            import Quartz

            # Dispatch an actual pixel scroll event through WebKit's native responder.
            # A JavaScript WheelEvent alone does not perform default scrolling.
            event = Quartz.CGEventCreateScrollWheelEvent(
                None, Quartz.kCGScrollEventUnitPixel, 1, int(value["delta"])
            )
            native = AppKit.NSEvent.eventWithCGEvent_(event)
            target = web.hitTest_((value["x"], web.bounds().size.height - value["y"]))
            (target or web).scrollWheel_(native)
            web.evaluateJavaScript_completionHandler_("window.__wheelRequest = null", None)
        AppHelper.callLater(0.05, wheel_poll)

    def wheel_poll():
        if not finished:
            web.evaluateJavaScript_completionHandler_("window.__wheelRequest || null", wheel_request)

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
        def home_snapshot(image, snapshot_error):
            if snapshot_error or image is None:
                return
            bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
            data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
            data.writeToFile_atomically_(str(output.with_name(output.stem + "-home.png")), True)

        web.takeSnapshotWithConfiguration_completionHandler_(
            WebKit.WKSnapshotConfiguration.alloc().init(), home_snapshot
        )
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
  assert(document.querySelectorAll('[data-view]').length === 2, 'GitHub navigation not unified');
  const field = id => document.getElementById(id);
  const edit = (element, value) => {
    const prototype = element.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event('input', {bubbles:true}));
  };
  const waitFor = async predicate => {
    for (let count = 0; count < 300; count++) { if (predicate()) return; await tick(); }
    throw new Error('Native response timed out');
  };
  // Measure actual WebKit scroll geometry, rather than infer it from CSS names.
  const checkViewport = () => {
    const root = document.scrollingElement;
    root.scrollTop = root.scrollHeight;
    assert(root.scrollTop === 0 && root.scrollHeight <= innerHeight + 1,
      `Blank document scroll: top=${root.scrollTop}, height=${root.scrollHeight}`);
    const main = document.querySelector('main');
    main.scrollTop = main.scrollHeight;
    const tail = main.lastElementChild.getBoundingClientRect().bottom;
    assert(main.scrollTop === 0 || tail >= main.getBoundingClientRect().bottom - 1,
      `Blank main scroll: content ends at ${tail}`);
    main.scrollTop = 0;
  };
  checkViewport();
  assert(field('send').disabled, 'Empty submission enabled');
  const handler = window.webkit.messageHandlers.prbot;
  const originalPost = handler.postMessage;
  const requests = [];
  handler.postMessage = body => requests.push(body);
  const complete = (action, data = {}) => {
    const request = requests.filter(item => item.action === action).at(-1);
    window.receiveNative({ok:true,completed_action:action,
      ...(request ? {bridge_id:request.bridge_id} : {}),...data});
  };
  // Drain a pre-existing history request, then queued correlated requests.
  complete('history'); await tick(); complete('history'); await tick();
  for (const link of ['https://github.com/owner/repo',
    'https://github.com/owner/repo/issues/19', 'https://github.com/owner/repo/pull/20']) {
    edit(field('target-link'),link); edit(field('draft'),'fix'); await tick();
    assert(!field('send').disabled, 'Unified target rejected: ' + link);
  }
  edit(field('target-link'),'https://github.com/owner/repo/actions'); await tick();
  assert(field('send').disabled, 'Unsupported link enabled');
  edit(field('target-link'),'https://github.com/owner/repo/pull/20'); await tick();
  field('composer').requestSubmit(); field('composer').requestSubmit(); await tick();
  const sent = requests.filter(request => request.action === 'submit_message');
  assert(sent.length === 1 && sent[0].mode === 'GitHub', 'Duplicate or wrong routing');
  assert(field('send').getAttribute('aria-label') === 'Submitting…');
  assert(document.querySelector('[data-state="submitting"]'), 'Submitting state missing');
  assert(document.querySelector('[aria-label="Conversation history"]').textContent.includes('fix'),
    'Submitted message not immediately shown');
  window.receiveNative({ok:true,status:{state:'stopped'}}); await tick();
  assert(field('send').getAttribute('aria-label') === 'Submitting…', 'Heartbeat released lock');
  complete('submit_message',{ok:false,message:'Message not saved. Retry.'}); await tick();
  assert(field('draft').value === 'fix' && !field('send').disabled, 'Failure lost draft');
  field('composer').requestSubmit(); await tick();
  const retry = requests.filter(request => request.action === 'submit_message').at(-1);
  assert(retry.request_id === sent[0].request_id, 'Retry changed task ID');
  // New during submission must not stop the task or let a late acceptance steal selection.
  field('new-session').click(); await tick();
  assert(field('draft').value === '' && field('target-link').value === '');
  const first = {id:retry.request_id,mode:'Pull Request',source:'desktop',status:'queued',
    conversation_id:retry.request_id,created_at:1,changed_at:1,
    user_text:'fix',title:'fix',events:[]};
  complete('submit_message',{accepted:{...first},tasks:[{...first}]});
  await tick();
  assert(!document.querySelector('[data-state="queued"]'), 'Late acceptance changed session');
  assert(document.querySelector('.activity-count').textContent === '1', 'Missing actual activity');
  field('history-toggle').click(); await tick();
  assert(field('history-sessions').hidden, 'History did not collapse');
  field('history-toggle').click(); await tick();
  document.querySelector(`[data-session="${first.id}"]`).click(); await tick();
  assert(!document.querySelector('.activity-count'),
    'Viewing session did not acknowledge activity');
  assert(field('send').getAttribute('aria-label') === 'Stop task' && !field('send').disabled);
  field('send').click(); field('send').click(); await tick();
  assert(requests.filter(item => item.action === 'cancel_task').length === 1, 'Duplicate stop');
  assert(requests.find(item => item.action === 'cancel_task').task_id === first.id,
    'Wrong stop ID');
  first.status = 'cancelling'; first.changed_at = 2;
  complete('cancel_task',{task_update:{...first}}); await tick();
  assert(field('send').getAttribute('aria-label') === 'Stopping…' && field('send').disabled,
    'Stop acknowledgement presented as terminal');
  first.status = 'cancelled'; first.changed_at = 3;
  window.receiveNative({ok:true,tasks:[{...first}]}); await tick();
  assert(field('send').getAttribute('aria-label') === 'Send',
    'Terminal state did not restore send');
  field('new-session').click(); await tick();
  edit(field('target-link'),'https://github.com/owner/repo'); edit(field('draft'),'keep me');
  await tick(); field('cancel-draft').click(); await tick();
  assert(document.querySelector('[role="alertdialog"]'), 'Draft loss has no confirmation');
  [...document.querySelectorAll('button')]
    .find(button => button.textContent === 'Keep editing').click();
  await tick(); assert(field('draft').value === 'keep me');
  field('cancel-draft').click(); await tick(); field('confirm-cancel').click(); await tick();
  assert(field('draft').value === '' && field('target-link').value === '', 'Cancel did not clear');
  assert(document.querySelector(`[data-session="${first.id}"]`), 'Cancel deleted history');
  // Restore an archived-only session and verify pagination and stale-response guards.
  const savedSessions = [{id:'remote-task',title:'Restored archived context',mode:'Issue',
    source:'desktop',created_at:2}, {id:first.id,title:'fix',mode:'Pull Request',
    source:'desktop',created_at:1}];
  const latestHistory = () => requests.filter(item => item.action === 'history').at(-1);
  const respondHistory = (request, data = {}) => window.receiveNative({ok:true,
    completed_action:'history',bridge_id:request.bridge_id,
    history:{mode:'All',session:request.session,request_id:request.request_id,
      cursor:'0',reset:true,sessions:savedSessions,messages:[],...data}});
  for (let i = 0; i < 5; i++) {
    const request = latestHistory();
    if (request) respondHistory(request);
    await tick();
    if (latestHistory() === request) break;
  }
  document.querySelector('[data-session="remote-task"]').click(); await tick();
  const oldRequest = latestHistory();
  respondHistory(oldRequest,{cursor:'next-page',messages:[{
    conversation_id:'legacy',message_id:'old-user',task_id:'remote-task',role:'user',
    source:'desktop',sequence:1,content:'Restored first line\\nSecond line'}]});
  await tick();
  const history = document.querySelector('[aria-label="Conversation history"]');
  assert(history.textContent.includes('Restored first line'), 'Archived messages not restored');
  assert(history.querySelectorAll('.history-message').length === 1, 'Duplicate saved message');
  [...history.querySelectorAll('button')]
    .find(button => button.textContent === 'Load more messages').click(); await tick();
  assert(latestHistory().cursor === 'next-page', 'History pagination cursor lost');
  respondHistory(latestHistory(),{reset:false,messages:[{
    conversation_id:'legacy',message_id:'older',task_id:'remote-task',role:'user',
    source:'desktop',sequence:2,content:'More archived context'}]}); await tick();
  assert(history.querySelectorAll('.history-message').length === 2, 'History page not appended');
  // Sending from an archived session must use a fresh task UUID, never the remote ID.
  edit(field('target-link'),'https://github.com/owner/repo');
  edit(field('draft'),'New task from saved context'); await tick();
  field('composer').requestSubmit(); await tick();
  const historicalSend = requests.filter(item => item.action === 'submit_message').at(-1);
  assert(historicalSend.request_id !== 'remote-task', 'Archived task ID reused for new work');
  complete('submit_message',{ok:false,message:'Fixture: not submitted'}); await tick();
  document.querySelector('[data-session="remote-task"]').click(); await tick();
  assert(field('draft').value === '', 'Sending copied the same draft into two sessions');
  field('new-session').click(); await tick();
  respondHistory(oldRequest,{messages:[{message_id:'stale',content:'Must not appear'}]});
  await tick();
  assert(!history.textContent.includes('Must not appear'), 'Stale history response won race');
  // Finish any queued request, then exercise error, retry and empty feedback.
  for (let i = 0; i < 5; i++) {
    const request = latestHistory(); if (request) respondHistory(request);
    await tick(); if (latestHistory() === request) break;
  }
  [...document.querySelectorAll('aside button')]
    .find(button => button.textContent === 'Refresh history').click(); await tick();
  const failedHistory = latestHistory();
  window.receiveNative({ok:false,completed_action:'history',bridge_id:failedHistory.bridge_id,
    history_error:{request_id:failedHistory.request_id,message:'Fixture history unavailable'}});
  await tick(); assert(history.querySelector('[role="alert"]'), 'Missing history error');
  [...history.querySelectorAll('button')]
    .find(button => button.textContent === 'Retry loading').click(); await tick();
  respondHistory(latestHistory(),{sessions:[],messages:[]}); await tick();
  assert(history.textContent.includes('No saved messages'), 'Missing empty history guidance');
  // A lost acceptance reply must be recovered from task polling, without reusing
  // the accepted task's key for new input or clearing edits made after timeout.
  field('new-session').click(); await tick();
  edit(field('target-link'),'https://github.com/owner/repo');
  edit(field('draft'),'Submission with lost reply'); await tick();
  field('composer').requestSubmit(); await tick();
  const lost = requests.filter(item => item.action === 'submit_message').at(-1);
  const realNow = Date.now;
  try {
    Date.now = () => realNow() + 120000;
    await waitFor(() => field('send').getAttribute('aria-label') === 'Send');
  } finally { Date.now = realNow; }
  edit(field('draft'),'New input after timeout'); await tick();
  const recovered = {id:lost.request_id,mode:'Issue',source:'desktop',status:'completed',
    conversation_id:lost.request_id,created_at:4,changed_at:5,
    user_text:'Submission with lost reply',title:'Recovered task',events:[]};
  window.receiveNative({ok:true,tasks:[recovered]}); await tick();
  assert(field('draft').value === 'New input after timeout', 'Polling lost unsent edits');
  window.receiveNative({ok:true,completed_action:'submit_message',bridge_id:lost.bridge_id,
    accepted:recovered}); await tick();
  assert(field('draft').value === 'New input after timeout', 'Late acceptance cleared edits');
  field('composer').requestSubmit(); await tick();
  const recoveredSend = requests.filter(item => item.action === 'submit_message').at(-1);
  assert(recoveredSend.request_id !== lost.request_id, 'Recovered submission reused old key');
  const acceptedRecovery = {...recovered,id:recoveredSend.request_id,
    conversation_id:recoveredSend.request_id,user_text:'New input after timeout'};
  window.receiveNative({ok:true,tasks:[recovered,acceptedRecovery]}); await tick();
  complete('submit_message',{accepted:acceptedRecovery,tasks:[recovered,acceptedRecovery]});
  await tick(); assert(field('draft').value === '', 'Known submitted input not cleared');
  handler.postMessage = originalPost;
  edit(field('draft'), 'Create documentation and explain acceptance criteria'); await tick();
  document.getElementById('user').click();
  await tick();
  assert(document.getElementById('settings').open);
  assert(document.activeElement.id === 'settings-title', 'Close button received initial focus');
  assert(!document.getElementById('close-settings').matches(':focus-visible'),
    'Close button highlighted');
  assert(document.querySelectorAll('[data-section]').length === 4);
  assert(document.querySelectorAll('.workspace-row').length === 2);
  const buttons = Array.from(document.querySelectorAll('.workspace-row button'));
  assert(buttons[0].getBoundingClientRect().width === buttons[1].getBoundingClientRect().width);
  assert(buttons[0].getBoundingClientRect().right === buttons[1].getBoundingClientRect().right);
  const select = async name => {
    const button = document.querySelector('[data-section="' + name + '"]');
    await waitFor(() => !button.disabled); button.click(); await tick();
  };
  const intercepted = handler.postMessage;
  handler.postMessage = body => {
    if (body.action === 'connections') window.__slowConnection = body;
    else intercepted.call(handler, body);
  };
  await select('Connections');
  assert(window.__slowConnection, 'Connections request not started');
  assert(!field('id-token').disabled, 'Connection refresh disabled token input');
  edit(field('id-token'), 'unsaved-connection-draft'); field('id-token').focus(); await tick();
  const tokenNode = field('id-token');
  const stable = {ok:true, status:{state:'stopped',loaded:false},tasks:[]};
  window.receiveNative(stable); await tick();
  let mutations = 0;
  const stableObserver = new MutationObserver(() => mutations++);
  stableObserver.observe(field('settings'),
    {subtree:true,attributes:true,childList:true,characterData:true});
  for (let index = 0; index < 4; index++) window.receiveNative(stable);
  await tick(); stableObserver.disconnect();
  assert(mutations === 0, 'Unchanged background status mutated Settings');
  assert(field('id-token') === tokenNode && document.activeElement === tokenNode,
    'Polling remounted or blurred connection input');
  const switchStart = performance.now();
  await select('Workspace');
  assert(performance.now() - switchStart < 500, 'Settings navigation blocked by connections');
  const pendingConnection = window.__slowConnection;
  window.receiveNative({ok:true,completed_action:'connections',
    bridge_id:pendingConnection.bridge_id,
    connections:{id_token:'refreshed-token',infisical:'Connected',project:'fixture'}});
  await tick(); await select('Connections');
  assert(field('id-token').value === 'unsaved-connection-draft',
    'Connection response overwrote draft');
  handler.postMessage = intercepted;
  const nativePaste = async element => {
    edit(element, ''); await tick(); element.focus();
    window.__pasteHandled = false; window.__pasteRequest = true;
    await waitFor(() => !window.__pasteRequest);
    await waitFor(() => element.value === 'native-paste-fixture');
    assert(window.__pasteHandled, 'Cmd+V menu shortcut was not handled');
    assert(document.activeElement === element, 'Native paste lost focus');
  };
  await nativePaste(field('id-token'));
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
  for (const key of Object.keys(examples)) await nativePaste(field(key));
  // Restore blank initial values after proving native paste reaches controlled fields.
  for (const key of ['INFISICAL_CLIENT_ID','INFISICAL_PROJECT_ID','INFISICAL_CLIENT_SECRET']) {
    edit(field(key), ''); await tick();
  }
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
  // Saving a draft reverted to its original values must still acknowledge success.
  edit(field('INFISICAL_SECRET_PATH'), '/temporary-draft'); await tick();
  edit(field('INFISICAL_SECRET_PATH'), updated.INFISICAL_SECRET_PATH); await tick();
  assert(!field('save-configuration').disabled, 'Reverted draft was not marked dirty');
  field('save-configuration').click(); await tick();
  await waitFor(() => !field('INFISICAL_URL').disabled && field('save-configuration').disabled);
  assertValues(updated, true);
  await select('Background Service');
  await tick();
  assert(document.querySelectorAll('#service-power').length === 1);
  assert(document.querySelector('.service-indicator'));
  assert(!document.querySelector('#settings .service-core'), 'Telegram still animates');
  window.receiveNative({ok:true, status:{state:'stale',loaded:true}});
  await tick();
  assert(document.getElementById('service-power').getAttribute('aria-label') === 'Refresh status');
  document.querySelector('[data-section="Workspace"]').click();
  await tick();
  document.getElementById('close-settings').click();
  await tick();
  assert(!document.getElementById('settings').open);
  await nativePaste(field('draft'));
  const statusArea = document.querySelector('[aria-label="Task status"]');
  assert(statusArea.compareDocumentPosition(field('composer'))
    & Node.DOCUMENT_POSITION_FOLLOWING, 'Execution feedback is below composer');
  assert(document.querySelector('.task-status').nextElementSibling === field('composer'),
    'Codex status must sit immediately above the composer');
  // History remains in the sidebar while the task workspace stays mounted.
  const historyRequests = [];
  handler.postMessage = body => historyRequests.push(body);
  field('history-toggle').click(); await tick();
  field('history-toggle').click(); await tick();
  assert(!field('history-sessions').hidden && field('composer'), 'History replaced workspace');
  document.querySelector('[data-view="Chat History"]').click(); await tick();
  assert(field('composer') && !field('target-link'), 'Private chat entry missing');
  document.querySelector('[data-view="GitHub"]').click(); await tick();
  handler.postMessage = originalPost;
  field('user').focus();
  // Keep a deterministic fixture visible for scroll, answer-routing and screenshot evidence.
  const routed = [];
  handler.postMessage = body => routed.push(body);
  const fixtureTask = {id:'ui-waiting-task',conversation_id:'ui',mode:'Issue',
    status:'needs_clarification',created_at:1,
    result:JSON.stringify({summary:'Which branch should I use?',outcome:'needs_clarification',
      pr_url:'',validation:'Waiting for your answer'}),
    events:Array.from({length:35},(_,index) => ({sequence:index,kind:'text',created:index+1,
      text:'Reviewed repository context · step ' + index}))};
  window.receiveNative({ok:true,tasks:[JSON.parse(JSON.stringify(fixtureTask))],
    tasks_revision:999999999}); await tick();
  document.querySelector('[data-session="ui-waiting-task"]').click(); await tick();
  assert(document.querySelector('.task-status').dataset.state === 'needs_clarification',
    'Task animation is not driven by task state');
  const feed = document.querySelector('.execution-feed');
  feed.scrollTop = 0; feed.dispatchEvent(new Event('scroll')); await tick();
  field('draft').focus();
  const draftValue = field('draft').value;
  fixtureTask.events.push({sequence:36,kind:'text',created:36,text:'Ready for your answer.'});
  window.receiveNative({ok:true,tasks:[JSON.parse(JSON.stringify(fixtureTask))],
    tasks_revision:999999999}); await tick();
  assert(feed.scrollTop === 0, 'New feedback interrupted historical reading');
  assert(document.activeElement === field('draft') && field('draft').value === draftValue,
    'Feedback changed composer focus or text');
  feed.scrollTop = feed.scrollHeight; feed.dispatchEvent(new Event('scroll')); await tick();
  fixtureTask.events.push({sequence:37,kind:'text',created:37,text:'Task safely paused.'});
  window.receiveNative({ok:true,tasks:[JSON.parse(JSON.stringify(fixtureTask))],
    tasks_revision:999999999}); await tick();
  assert(feed.scrollHeight - feed.scrollTop - feed.clientHeight < 2,
    'Feedback did not follow near the bottom');
  edit(field('answer-ui-waiting-task'), 'Use feature/ui'); await tick();
  field('new-session').click(); await tick();
  document.querySelector('[data-session="ui-waiting-task"]').click(); await tick();
  assert(field('answer-ui-waiting-task').value === 'Use feature/ui',
    'Session navigation lost the unsent answer');
  const answerForm = field('answer-ui-waiting-task').closest('form');
  answerForm.requestSubmit(); await tick();
  const reply = routed.find(body => body.action === 'answer');
  assert(reply && reply.task_id === fixtureTask.id && reply.text === 'Use feature/ui',
    'Answer was submitted as an unrelated new task');
  window.receiveNative({ok:false,completed_action:'answer',bridge_id:reply.bridge_id,
    message:'Fixture: waiting for your answer'}); await tick();
  assert(field('answer-ui-waiting-task').value === 'Use feature/ui', 'Failed reply lost draft');
  field('new-session').click(); await tick();
  document.querySelector('[data-session="ui-waiting-task"]').click(); await tick();
  assert(field('answer-ui-waiting-task').value === 'Use feature/ui',
    'Session navigation lost a retryable answer');
  field('answer-ui-waiting-task').closest('form').requestSubmit(); await tick();
  const retriedReply = routed.filter(body => body.action === 'answer').at(-1);
  assert(retriedReply.request_id === reply.request_id,
    'Session navigation changed the pending answer retry key');
  window.receiveNative({ok:false,completed_action:'answer',bridge_id:retriedReply.bridge_id,
    message:'Fixture: waiting for your answer'}); await tick();
  // A complete answer round can happen between polls. A new question must not
  // reuse the previous round's text or idempotency key.
  const nextQuestion = {...fixtureTask, changed_at:100,
    result:JSON.stringify({summary:'Which base branch?',outcome:'needs_clarification',
      pr_url:'',validation:'Waiting for another answer'})};
  window.receiveNative({ok:true,tasks:[nextQuestion],tasks_revision:999999999}); await tick();
  assert(field('answer-ui-waiting-task').value === '', 'New question retained old answer');
  edit(field('answer-ui-waiting-task'), 'Use main'); await tick();
  field('answer-ui-waiting-task').closest('form').requestSubmit(); await tick();
  const nextReply = routed.filter(body => body.action === 'answer').at(-1);
  assert(nextReply.request_id !== reply.request_id, 'New question reused prior answer key');
  window.receiveNative({ok:false,completed_action:'answer',bridge_id:nextReply.bridge_id,
    message:'Fixture: waiting for another answer'}); await tick();
  const remoteTask = {...fixtureTask, id:'ui-telegram-task', source:'telegram', created_at:2};
  window.receiveNative({ok:true,tasks:[fixtureTask,remoteTask],tasks_revision:999999999});
  await tick();
  assert(!field('answer-ui-telegram-task'), 'Desktop can answer another Telegram identity');
  document.querySelector('[data-session="ui-telegram-task"]').click(); await tick();
  assert(document.querySelector('[aria-label="Task status"]').textContent.includes(
    'Reply to this task in Telegram'), 'Missing Telegram reply guidance');
  assert(field('send').disabled, 'Desktop Stop available for Telegram task');
  document.querySelector('[data-session="ui-waiting-task"]').click(); await tick();
  assert(document.querySelector('.task-status').textContent.includes(fixtureTask.id),
    'Current task selection does not drive Codex status');
  window.receiveNative({ok:true,tasks:[fixtureTask],tasks_revision:999999999}); await tick();
  checkViewport();
  const mainScroll = document.querySelector('main');
  const wheel = async (element, delta) => {
    const bounds = element.getBoundingClientRect();
    window.__wheelRequest = {x:bounds.left + 20,y:bounds.top + 20,delta};
    await waitFor(() => !window.__wheelRequest); await tick();
  };
  mainScroll.scrollTop = 0;
  await wheel(mainScroll, -900);
  assert(mainScroll.scrollTop > 0, 'Native wheel did not scroll long task content');
  for (let count = 0; count < 4; count++) await wheel(mainScroll, -900);
  assert(document.scrollingElement.scrollTop === 0, 'Native wheel escaped into blank document');
  assert(mainScroll.scrollTop <= mainScroll.scrollHeight - mainScroll.clientHeight + 1,
    'Native wheel exceeded actual content');
  checkViewport();
  document.querySelector('main').scrollTop = document.querySelector('main').scrollHeight;
  field('user').focus();
  assert(document.activeElement.id === 'user', 'Dialog did not restore focus');
  assert(!/[\u4e00-\u9fff]/.test(document.body.innerText), 'Non-English product copy');
  window.__uiCheckResult = 'ok';
})().catch(error => { window.__uiCheckResult = 'failed:' + error.message; });
true;
""",
            lambda result, error: poll_result(),
        )

    AppHelper.callLater(0.25, poll)
    AppHelper.callLater(0.25, paste_poll)
    AppHelper.callLater(0.25, wheel_poll)
    AppHelper.callLater(75, lambda: finish(False, "UI load or snapshot timed out"))
