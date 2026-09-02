/* GeekPark Mesh · 统一对话框（替代浏览器 confirm/prompt/alert） */
(function () {
  if (window.MeshDialog) return;

  var root = null;
  var titleEl, bodyEl, inputWrap, inputEl, errEl, cancelBtn, okBtn;
  var resolver = null;
  var mode = "confirm";
  var expect = "";

  function ensure() {
    if (root) return;
    root = document.createElement("div");
    root.className = "mesh-dlg-mask";
    root.hidden = true;
    root.innerHTML =
      '<div class="mesh-dlg" role="dialog" aria-modal="true">' +
      "<h3></h3>" +
      '<p class="mesh-dlg-body"></p>' +
      '<div class="mesh-dlg-input" hidden><input type="text" autocomplete="off"></div>' +
      '<p class="mesh-dlg-err" hidden></p>' +
      '<div class="row">' +
      '<button type="button" class="c" data-act="cancel">取消</button>' +
      '<button type="button" class="k" data-act="ok">确定</button>' +
      "</div></div>";
    document.body.appendChild(root);
    titleEl = root.querySelector("h3");
    bodyEl = root.querySelector(".mesh-dlg-body");
    inputWrap = root.querySelector(".mesh-dlg-input");
    inputEl = inputWrap.querySelector("input");
    errEl = root.querySelector(".mesh-dlg-err");
    cancelBtn = root.querySelector('[data-act="cancel"]');
    okBtn = root.querySelector('[data-act="ok"]');

    cancelBtn.addEventListener("click", function () {
      close(null);
    });
    okBtn.addEventListener("click", function () {
      onOk();
    });
    root.addEventListener("click", function (e) {
      if (e.target === root) close(null);
    });
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        onOk();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (!root || root.hidden) return;
      if (e.key === "Escape") close(null);
    });
  }

  function onOk() {
    if (mode === "prompt") {
      var v = (inputEl.value || "").trim();
      if (expect && v !== expect) {
        errEl.hidden = false;
        errEl.textContent = "输入不匹配，请输入「" + expect + "」";
        inputEl.focus();
        return;
      }
      close(v);
      return;
    }
    close(true);
  }

  function close(value) {
    if (!root) return;
    root.hidden = true;
    root.classList.remove("on");
    var r = resolver;
    resolver = null;
    if (r) r(value);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatBody(raw) {
    // 允许调用方直接传 HTML（含 <br>）；纯文本则转义并保留换行
    var s = String(raw || "");
    if (/<[a-z][\s\S]*>/i.test(s)) return s;
    return escapeHtml(s).replace(/\r\n|\r|\n/g, "<br>");
  }

  function open(opts) {
    ensure();
    return new Promise(function (resolve) {
      // 若上一个还开着，先关掉再开，避免叠两层
      if (resolver) {
        var prev = resolver;
        resolver = null;
        prev(null);
      }
      resolver = resolve;
      mode = opts.mode || "confirm";
      expect = opts.expect || "";
      titleEl.textContent = opts.title || "";
      bodyEl.innerHTML = formatBody(opts.body || "");
      bodyEl.hidden = !opts.body;
      errEl.hidden = true;
      errEl.textContent = "";
      cancelBtn.hidden = !!opts.hideCancel;
      cancelBtn.textContent = opts.cancelText || "取消";
      okBtn.textContent = opts.okText || "确定";
      okBtn.classList.toggle("danger", !!opts.danger);
      if (mode === "prompt") {
        inputWrap.hidden = false;
        inputEl.value = "";
        inputEl.placeholder = opts.placeholder || "";
      } else {
        inputWrap.hidden = true;
      }
      // 同步显示，不等 setTimeout / blur 构图
      root.hidden = false;
      root.classList.add("on");
      requestAnimationFrame(function () {
        if (mode === "prompt") inputEl.focus();
        else okBtn.focus();
      });
    });
  }

  window.MeshDialog = {
    confirm: function (opts) {
      if (typeof opts === "string") opts = { title: "请确认", body: opts };
      return open(
        Object.assign({ mode: "confirm", okText: "确定", cancelText: "取消" }, opts || {})
      ).then(function (v) {
        return v === true;
      });
    },
    prompt: function (opts) {
      if (typeof opts === "string") opts = { title: "请输入", placeholder: opts };
      return open(
        Object.assign({ mode: "prompt", okText: "确定", cancelText: "取消" }, opts || {})
      );
    },
    alert: function (opts) {
      if (typeof opts === "string") opts = { title: "提示", body: opts };
      return open(
        Object.assign(
          { mode: "alert", okText: "知道了", hideCancel: true },
          opts || {}
        )
      ).then(function () {
        return true;
      });
    },
    warm: function () {
      ensure();
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      MeshDialog.warm();
    });
  } else {
    MeshDialog.warm();
  }
})();
