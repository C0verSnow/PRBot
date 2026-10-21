"""Remote CI only: exercise the packaged WebKit UI and export a real window snapshot."""

import json


def schedule_check(web, output):
    import AppKit
    import WebKit
    from PyObjCTools import AppHelper

    finished = False

    def finish(ok, detail):
        nonlocal finished
        if finished:
            return
        finished = True
        output.with_suffix(".json").write_text(json.dumps({"ok": ok, "detail": detail}))
        AppHelper.stopEventLoop()

    def snapshot(image, error):
        if error or image is None:
            finish(False, "WebKit snapshot failed")
            return
        bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        ok = bool(data.writeToFile_atomically_(str(output), True))
        finish(ok, "React DOM, navigation, settings, native bridge and snapshot checked")

    def checked(result, error):
        if finished:
            return
        if error or result == "failed":
            finish(False, "UI interaction check failed")
        elif result == "ok":
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
            " && document.getElementById('service-label').textContent !== '查看服务状态'",
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
  const assert = value => { if (!value) throw new Error('UI assertion failed'); };
  const tick = () => new Promise(resolve => setTimeout(resolve, 40));
  assert(getComputedStyle(document.querySelector('#root > div')).display === 'flex');
  assert(document.querySelector('aside').getBoundingClientRect().width === 252);
  assert(getComputedStyle(document.querySelector('#composer')).borderRadius === '17px');
  assert(document.querySelector('#greeting').textContent === 'What shall we think through?');
  assert(document.querySelectorAll('[data-view]').length === 6);
  assert(document.querySelectorAll('.composer button').length === 1);
  for (const view of ['Projects', 'Artifacts', 'Code', 'Pinned', 'Chats and tasks']) {
    document.querySelector(`[data-view="${view}"]`).click();
    await tick();
    assert(!document.getElementById('empty-view').hidden);
    assert(document.getElementById('empty-description').textContent.length > 15);
    assert(document.querySelector(`[data-view="${view}"]`).getAttribute('aria-current'));
  }
  document.getElementById('back-new').click();
  await tick();
  assert(!document.getElementById('new-view').hidden);
  const input = document.getElementById('draft');
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(
    input, 'https://github.com/owner/repo 创建文档并说明验收要求');
  input.dispatchEvent(new Event('input', {bubbles:true}));
  await tick();
  document.getElementById('user').click();
  await tick();
  assert(document.getElementById('settings').open);
  assert(document.querySelectorAll('[data-action]').length === 11);
  document.getElementById('close-settings').click();
  await tick();
  assert(!document.getElementById('settings').open);
  assert(input.value.includes('owner/repo'));
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '');
  input.dispatchEvent(new Event('input', {bubbles:true}));
  await tick();
  window.__uiCheckResult = 'ok';
})().catch(() => { window.__uiCheckResult = 'failed'; });
true;
""",
            lambda result, error: poll_result(),
        )

    AppHelper.callLater(0.25, poll)
    AppHelper.callLater(45, lambda: finish(False, "UI load or snapshot timed out"))
