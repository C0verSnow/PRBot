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
        finish(ok, "Bundled DOM, navigation, settings, native bridge and snapshot checked")

    def checked(result, error):
        if error or result != "ok":
            finish(False, "UI interaction check failed")
            return
        web.takeSnapshotWithConfiguration_completionHandler_(
            WebKit.WKSnapshotConfiguration.alloc().init(), snapshot
        )

    def poll():
        if finished:
            return
        web.evaluateJavaScript_completionHandler_(
            "document.readyState === 'complete' && typeof window.receiveNative === 'function'"
            " && document.getElementById('service-label').textContent !== '查看服务状态'",
            loaded,
        )

    def loaded(result, error):
        if not result or error:
            AppHelper.callLater(0.25, poll)
            return
        # Native ready/status bridge has completed. No credentials or daemon operations invoked.
        web.evaluateJavaScript_completionHandler_(
            """
(() => {
  const assert = value => { if (!value) throw new Error('UI assertion failed'); };
  assert(document.querySelector('#greeting').textContent === 'What shall we think through?');
  assert(document.querySelectorAll('[data-view]').length === 6);
  assert(document.querySelectorAll('.composer button').length === 1);
  for (const view of ['Projects', 'Artifacts', 'Code', 'Pinned', 'Chats and tasks']) {
    document.querySelector(`[data-view="${view}"]`).click();
    assert(!document.getElementById('empty-view').hidden);
    assert(document.getElementById('empty-description').textContent.length > 15);
    assert(document.querySelector(`[data-view="${view}"]`).getAttribute('aria-current'));
  }
  document.getElementById('back-new').click();
  document.getElementById('draft').value = 'https://github.com/owner/repo 创建文档并说明验收要求';
  document.getElementById('user').click();
  assert(document.getElementById('settings').open);
  assert(document.querySelectorAll('[data-action]').length === 11);
  document.getElementById('close-settings').click();
  assert(!document.getElementById('settings').open);
  assert(document.getElementById('draft').value.includes('owner/repo'));
  document.getElementById('draft').value = '';
  return 'ok';
})()
""",
            checked,
        )

    AppHelper.callLater(0.25, poll)
    AppHelper.callLater(45, lambda: finish(False, "UI load or snapshot timed out"))
