"""Remote CI only: exercise the packaged WebKit UI and export a real window snapshot."""

import json
import os


def schedule_check(web, output):
    import AppKit
    import WebKit
    from PyObjCTools import AppHelper

    finished = False
    interaction_error = None
    minimum = False

    def finish(ok, detail):
        nonlocal finished
        if finished:
            return
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
  document.getElementById('user').click();
  await tick();
  assert(document.getElementById('settings').open);
  assert(document.querySelectorAll('[data-section]').length === 3);
  assert(document.querySelectorAll('.workspace-row').length === 2);
  const buttons = Array.from(document.querySelectorAll('.workspace-row button'));
  assert(buttons[0].getBoundingClientRect().width === buttons[1].getBoundingClientRect().width);
  assert(buttons[0].getBoundingClientRect().right === buttons[1].getBoundingClientRect().right);
  document.querySelector('[data-section="Background Service"]').click();
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
