// SPDX-License-Identifier: MIT
// Adapted from PDF Toolbox, copyright (c) 2026 the PDF Toolbox authors, MIT License.
package com.merrimentlabs.excalibook;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.util.Log;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.ConsoleMessage;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebMessageCompat;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONException;
import org.json.JSONObject;

/** The Excalibook window: Excalidraw in a WebView, with Android's files and downloads. */
public class MainActivity extends Activity {
  static final String TAG = Web.TAG;
  private static final int REQ_FILES = 1;
  private static final int CHUNK = 4 << 20;

  private final Handler main = new Handler(Looper.getMainLooper());
  /** Downloads and incoming files, in order, off the main thread. */
  private final ExecutorService io = Executors.newSingleThreadExecutor();
  private Store store;
  private FrameLayout root;
  private WebView web;
  private ValueCallback<Uri[]> pendingFiles;
  /** The file being saved; only touched on the io thread. */
  private Downloads download;
  /** A drawing being saved back to the file it was opened from (io thread). */
  private WriteBack writeBack;
  /**
   * Files opened with Excalibook that it may write to, by the token the page
   * got with each one: Ctrl+S on such a drawing saves back to it.
   */
  private final java.util.Map<String, Uri> writable = new java.util.HashMap<>();
  /** Test files the app wrote to Download itself (debug "incoming"): it may write them. */
  private final java.util.Set<Uri> own = java.util.Collections.synchronizedSet(new java.util.HashSet<>());
  private final List<Uri> incoming = new ArrayList<>();
  /** The page's channel, once it has said it's ready for files. */
  private JavaScriptReplyProxy page;
  private BroadcastReceiver debug;
  private View savedBar;
  /** Back while a dialog, menu or sidebar is open closes that, not the window. */
  private final android.window.OnBackInvokedCallback closeOverlay =
      () -> web.evaluateJavascript("window.excalibookApp && window.excalibookApp.back()", null);
  private boolean overlayOpen;

  @Override
  protected void onCreate(Bundle saved) {
    super.onCreate(saved);
    store = new Store(this);
    WebView.setWebContentsDebuggingEnabled(store.devtools());

    root = new FrameLayout(this);
    setContentView(root);
    // Keep the page out from under the caption bar: its buttons would cover
    // Excalidraw's menu and toolbar. The caption shows root's color, which
    // follows the canvas (applyWindowColors).
    root.setOnApplyWindowInsetsListener((v, insets) -> {
      android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars()
          | WindowInsets.Type.captionBar() | WindowInsets.Type.displayCutout());
      v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
      return WindowInsets.CONSUMED;
    });
    createWebView();
    applyWindowColors(store.theme());
    registerDebugCommands();
    takeIncoming(getIntent());
    web.loadUrl(Web.HOME);
  }

  private void createWebView() {
    web = new WebView(this);
    Web.configure(web, this::onPageMessage);
    web.setWebViewClient(new Client());
    web.setWebChromeClient(new Chrome());
    web.setBackgroundColor(store.theme());
    root.addView(web, 0, new FrameLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
  }

  @Override
  protected void onNewIntent(Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    takeIncoming(intent);
    if (page != null) offerIncoming(page);
  }

  @Override
  protected void onPause() {
    super.onPause();
    // The page also saves on every change and when it's hidden; this is one
    // more chance before Android might stop the app.
    web.evaluateJavascript("window.excalibookApp && window.excalibookApp.flush()", null);
  }

  @Override
  protected void onDestroy() {
    if (debug != null) unregisterReceiver(debug);
    io.execute(() -> {
      abortDownload();
      writeBack = null;
    });
    io.shutdown();
    web.destroy();
    super.onDestroy();
  }

  /**
   * Escape belongs to the drawing (cancel a tool, close a menu). If the page
   * leaves it unhandled, Android would turn it into Back and close the window.
   */
  @Override
  public boolean dispatchKeyEvent(KeyEvent event) {
    if (event.getKeyCode() == KeyEvent.KEYCODE_ESCAPE && web != null) {
      web.dispatchKeyEvent(event);
      return true;
    }
    return super.dispatchKeyEvent(event);
  }

  // ---- Files from "Open with" and "Share" ----

  private void takeIncoming(Intent intent) {
    List<Uri> uris = new ArrayList<>();
    String action = intent == null ? null : intent.getAction();
    if ((Intent.ACTION_VIEW.equals(action) || Intent.ACTION_EDIT.equals(action)) && intent.getData() != null) {
      uris.add(intent.getData());
    } else if (Intent.ACTION_SEND.equals(action) || Intent.ACTION_SEND_MULTIPLE.equals(action)) {
      ClipData clip = intent.getClipData();
      if (clip != null) {
        for (int i = 0; i < clip.getItemCount(); i++) {
          Uri u = clip.getItemAt(i).getUri();
          if (u != null) uris.add(u);
        }
      }
      if (uris.isEmpty() && intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class) != null) {
        uris.add(intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class));
      }
    }
    uris.removeIf(u -> !"content".equals(u.getScheme()));
    if (uris.isEmpty()) return;
    incoming.addAll(uris); // added to any that are still waiting, never replacing them
    Log.i(TAG, "incoming: " + uris.size() + " file(s)");
  }

  /** Files larger than this are refused (the page holds a whole file in memory). */
  private static final long MAX_FILE = 100L << 20;

  /**
   * Streams the waiting files to the page as one batch: begin, then a header
   * and chunks per file, then end. The whole batch is one io job, so a second
   * batch can't start inside the first.
   */
  private void offerIncoming(JavaScriptReplyProxy reply) {
    if (incoming.isEmpty()) return;
    List<Uri> uris = new ArrayList<>(incoming);
    incoming.clear();
    onIo(() -> {
      main.post(() -> reply.postMessage(json("type", "incoming.begin")));
      try {
        for (Uri uri : uris) {
          try {
            sendFile(uri, reply);
          } catch (IOException | RuntimeException e) {
            Log.w(TAG, "can't read " + uri, e);
            main.post(() -> toast(getString(R.string.cant_read)));
          }
        }
      } finally {
        main.post(() -> reply.postMessage(json("type", "incoming.end")));
      }
    });
  }

  private void sendFile(Uri uri, JavaScriptReplyProxy reply) throws IOException {
    String name = "file";
    long size = -1;
    try (Cursor c = getContentResolver().query(uri,
        new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
      if (c != null && c.moveToFirst()) {
        if (c.getString(0) != null) name = c.getString(0);
        if (!c.isNull(1)) size = c.getLong(1);
      }
    } catch (RuntimeException e) {
      Log.w(TAG, "no name for " + uri, e);
    }
    if (size > MAX_FILE) {
      String n = name;
      main.post(() -> toast(getString(R.string.too_large, n)));
      return;
    }
    String mime = getContentResolver().getType(uri);
    String token = canWrite(uri) ? java.util.UUID.randomUUID().toString() : "";
    if (!token.isEmpty()) main.post(() -> writable.put(token, uri));
    String header = json("type", "incoming.file", "name", name, "mime", mime == null ? "" : mime,
        "token", token);
    main.post(() -> reply.postMessage(header));
    try (InputStream in = getContentResolver().openInputStream(uri)) {
      if (in == null) throw new IOException("no stream");
      byte[] buf = new byte[CHUNK];
      long total = 0;
      for (int n; (n = in.readNBytes(buf, 0, CHUNK)) > 0; ) {
        total += n;
        if (total > MAX_FILE) throw new IOException(name + " is larger than " + MAX_FILE + " bytes");
        byte[] chunk = n == CHUNK ? buf.clone() : java.util.Arrays.copyOf(buf, n);
        main.post(() -> reply.postMessage(chunk));
      }
    }
  }

  /**
   * Runs work on the io thread. A provider can throw runtime exceptions
   * (a volume gone, a revoked grant); on the io thread they would end the
   * whole app, so they are caught, logged and shown.
   */
  private void onIo(Runnable work) {
    io.execute(() -> {
      try {
        work.run();
      } catch (RuntimeException e) {
        Log.e(TAG, "background file work failed", e);
        main.post(() -> toast(getString(R.string.file_error)));
      }
    });
  }

  // ---- Messages from the page ----

  private void onPageMessage(WebMessageCompat message, JavaScriptReplyProxy reply) {
    if (message.getType() == WebMessageCompat.TYPE_ARRAY_BUFFER) {
      byte[] chunk = message.getArrayBuffer();
      onIo(() -> {
        if (writeBack != null) {
          writeBack.write(chunk);
          return;
        }
        if (download == null) return;
        try {
          download.write(chunk);
        } catch (IOException e) {
          Log.w(TAG, "download failed", e);
          String name = download.name;
          abortDownload();
          main.post(() -> toast(getString(R.string.save_failed, name)));
        }
      });
      return;
    }
    String data = message.getData();
    if (data == null) return;
    JSONObject m;
    try {
      m = new JSONObject(data);
    } catch (JSONException e) {
      return;
    }
    switch (m.optString("type")) {
      case "download" -> startDownload(m.optString("name", "download"), m.optString("mime"), m.optLong("size", -1));
      case "download.end" -> finishDownload();
      case "writeback" -> startWriteBack(m.optString("token"), m.optLong("size", -1), reply);
      case "writeback.end" -> finishWriteBack(m.optString("token"));
      case "download.failed" -> {
        onIo(this::abortDownload);
        toast(getString(R.string.save_failed, m.optString("name")));
      }
      case "theme" -> {
        try {
          int color = Color.parseColor(m.optString("color"));
          store.setTheme(color);
          applyWindowColors(color);
        } catch (IllegalArgumentException ignored) {
          // not a color
        }
      }
      case "ready" -> {
        page = reply;
        setOverlay(false); // a fresh page has nothing open
        Log.i(TAG, "page ready");
        offerIncoming(reply);
      }
      case "overlay" -> setOverlay(m.optBoolean("open"));
      case "incoming.used" -> Log.i(TAG, "page took " + m.optInt("count") + " file(s)");
      default -> { }
    }
  }

  // The page sends a download as a header, its chunks and an end marker. The
  // main thread queues all three in arrival order on the single io thread.
  private void startDownload(String name, String mime, long size) {
    onIo(() -> {
      abortDownload();
      try {
        download = new Downloads(this, name, mime, size);
      } catch (IOException e) {
        Log.w(TAG, "can't create " + name, e);
        main.post(() -> toast(getString(R.string.save_failed, name)));
      }
    });
  }

  private void finishDownload() {
    onIo(() -> {
      Downloads d = download;
      download = null;
      if (d == null) return;
      try {
        Uri uri = d.finish();
        String shown = Downloads.displayName(this, uri, d.name);
        main.post(() -> showSaved(shown, uri));
      } catch (IOException e) {
        Log.w(TAG, "download failed", e);
        main.post(() -> toast(getString(R.string.save_failed, d.name)));
      }
    });
  }

  private void setOverlay(boolean open) {
    if (open == overlayOpen) return;
    overlayOpen = open;
    if (open) {
      getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
          android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, closeOverlay);
    } else {
      getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(closeOverlay);
    }
  }

  // ---- Saving back to an opened file ----

  /** Whether Android let this app write to the file it was handed. */
  private boolean canWrite(Uri uri) {
    return own.contains(uri) || checkUriPermission(uri, android.os.Process.myPid(), android.os.Process.myUid(),
        Intent.FLAG_GRANT_WRITE_URI_PERMISSION) == android.content.pm.PackageManager.PERMISSION_GRANTED;
  }

  /** The bytes of one save, collected in memory, then written in one go. */
  private static final class WriteBack {
    final String token;
    final Uri uri;
    final long size;
    final JavaScriptReplyProxy reply;
    final java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();

    WriteBack(String token, Uri uri, long size, JavaScriptReplyProxy reply) {
      this.token = token;
      this.uri = uri;
      this.size = size;
      this.reply = reply;
    }

    void write(byte[] chunk) {
      bytes.write(chunk, 0, chunk.length);
    }
  }

  private void startWriteBack(String token, long size, JavaScriptReplyProxy reply) {
    Uri uri = writable.get(token);
    onIo(() -> writeBack = new WriteBack(token, uri, size, reply));
  }

  /**
   * Writes a save back to its file once every byte has arrived. "wt"
   * truncates the file as it opens, so the file's current bytes are read
   * first; if the write then fails (storage full, volume gone), they are
   * written back, and the page is told the save failed.
   */
  private void finishWriteBack(String token) {
    onIo(() -> {
      WriteBack w = writeBack;
      writeBack = null;
      if (w == null) return;
      String error = null;
      if (w.uri == null) {
        error = "unknown file";
      } else if (!w.token.equals(token)) {
        error = "mixed up saves";
      } else if (w.size >= 0 && w.bytes.size() != w.size) {
        error = "got " + w.bytes.size() + " of " + w.size + " bytes";
      } else {
        byte[] original = null;
        try (InputStream in = getContentResolver().openInputStream(w.uri)) {
          if (in != null) original = in.readAllBytes();
        } catch (IOException | RuntimeException e) {
          Log.w(TAG, "can't read " + w.uri + " before saving over it", e);
          error = "can't read the file before saving over it";
        }
        if (error == null) {
          try (java.io.OutputStream out = openForWrite(w.uri)) {
            w.bytes.writeTo(out);
          } catch (IOException | RuntimeException e) {
            Log.w(TAG, "can't save back to " + w.uri, e);
            error = String.valueOf(e.getMessage());
            if (original != null) {
              try (java.io.OutputStream out = openForWrite(w.uri)) {
                out.write(original);
                error += " (the file was left as it was)";
              } catch (IOException | RuntimeException e2) {
                Log.e(TAG, "can't put back " + w.uri, e2);
                error += " (the file may be damaged; the drawing is still in Excalibook)";
              }
            }
          }
        }
      }
      if (error != null && w.uri != null) {
        String t = w.token;
        main.post(() -> writable.remove(t)); // its next save asks where to save
      }
      String done = error == null
          ? json("type", "writeback.done", "token", w.token, "ok", "true")
          : json("type", "writeback.done", "token", w.token, "ok", "false", "error", error);
      Log.i(TAG, "save back " + (error == null ? "ok, " + w.bytes.size() + " bytes" : "failed: " + error));
      main.post(() -> w.reply.postMessage(done));
    });
  }

  // Only "wt": plain "w" doesn't truncate with every provider, and a shorter
  // drawing would leave the old file's tail behind. A provider without "wt"
  // fails the save, and the page offers Save as instead.
  private java.io.OutputStream openForWrite(Uri uri) throws IOException {
    java.io.OutputStream out = getContentResolver().openOutputStream(uri, "wt");
    if (out == null) throw new IOException("no stream for " + uri);
    return out;
  }

  private void abortDownload() {
    if (download != null) download.abort();
    download = null;
  }

  /** A bar at the bottom of the window: the saved file, with Open and Show. */
  private void showSaved(String name, Uri uri) {
    if (savedBar != null) root.removeView(savedBar);
    LinearLayout bar = new LinearLayout(this);
    bar.setGravity(Gravity.CENTER_VERTICAL);
    bar.setPadding(dp(12), dp(6), dp(6), dp(6));
    GradientDrawable bg = new GradientDrawable();
    bg.setColor(0xF0202124);
    bg.setCornerRadius(dp(10));
    bar.setBackground(bg);
    bar.setElevation(dp(6));
    TextView text = new TextView(this);
    text.setText(getString(R.string.saved, name));
    text.setTextColor(0xFFF1F3F4);
    text.setTextSize(14);
    text.setMaxLines(2);
    bar.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
    bar.addView(button(R.string.open, v -> {
      Intent view = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, getContentResolver().getType(uri))
          .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
      try {
        startActivity(view);
      } catch (ActivityNotFoundException e) {
        toast(getString(R.string.no_app));
      }
    }));
    bar.addView(button(R.string.show, v -> {
      try {
        startActivity(new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS));
      } catch (ActivityNotFoundException e) {
        toast(getString(R.string.no_app));
      }
    }));
    FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
        Math.min(dp(560), root.getWidth() - dp(32)), ViewGroup.LayoutParams.WRAP_CONTENT,
        Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
    lp.bottomMargin = dp(16);
    root.addView(bar, lp);
    savedBar = bar;
    main.postDelayed(() -> {
      if (savedBar == bar) {
        root.removeView(bar);
        savedBar = null;
      }
    }, 12000);
  }

  private TextView button(int label, View.OnClickListener click) {
    TextView b = new TextView(this);
    b.setText(label);
    b.setTextColor(0xFF8AB4F8);
    b.setTextSize(14);
    b.setAllCaps(false);
    b.setPadding(dp(12), dp(8), dp(12), dp(8));
    b.setOnClickListener(click);
    b.setFocusable(true);
    b.setBackgroundResource(android.R.drawable.list_selector_background);
    return b;
  }

  // ---- WebView clients ----

  private final class Client extends WebViewClient {
    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
      Uri url = request.getUrl();
      if (Web.isApp(url)) return Web.serve(MainActivity.this, url);
      String scheme = url.getScheme();
      if ("http".equals(scheme) || "https".equals(scheme)) return Web.blocked(url);
      return null; // data: and blob: stay in the page
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
      Uri url = request.getUrl();
      if (Web.isApp(url)) {
        // The editor is the only page the window shows; a link to another
        // file of the app (in a drawing, say) is ignored. About loads in a
        // frame, which this doesn't cover.
        String path = url.getPath();
        boolean editor = path == null || path.isEmpty() || path.equals("/") || path.equals("/index.html");
        if (request.isForMainFrame() && !editor) {
          Log.w(TAG, "ignored navigation to " + url);
          return true;
        }
        return false;
      }
      // Links in drawings and Help's links go to the browser.
      try {
        startActivity(new Intent(Intent.ACTION_VIEW, url).addCategory(Intent.CATEGORY_BROWSABLE));
      } catch (ActivityNotFoundException e) {
        toast(getString(R.string.no_app));
      }
      return true;
    }

    @Override
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
      // A huge drawing can push the renderer out of memory. Replace the
      // WebView instead of letting the whole app die with it; the drawing
      // comes back from the app's storage.
      Log.w(TAG, "renderer gone, crashed=" + detail.didCrash());
      root.removeView(web);
      web.destroy();
      page = null;
      setOverlay(false);
      // Nothing of the dead page carries over: open transfers, file tokens,
      // a picker's callback.
      onIo(() -> {
        abortDownload();
        writeBack = null;
      });
      writable.clear();
      pendingFiles = null;
      createWebView();
      web.loadUrl(Web.HOME);
      toast(getString(R.string.renderer_gone));
      return true;
    }
  }

  private final class Chrome extends WebChromeClient {
    @Override
    public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
      if (pendingFiles != null) pendingFiles.onReceiveValue(null);
      pendingFiles = callback;
      Log.i(TAG, "file chooser: mode=" + params.getMode() + " types=" + String.join(",", params.getAcceptTypes())
          + (Web.SAVE_PICKER ? " name=" + params.getFilenameHint() : ""));
      try {
        // At targetSdk 37 this is ACTION_OPEN_DOCUMENT, or CREATE_DOCUMENT
        // for showSaveFilePicker (Save, Save as, Export), with the page's types.
        Intent intent = params.createIntent();
        // WebView leaves the suggested name out of the save picker's intent;
        // without it DocumentsUI offers an empty name box.
        if (Web.SAVE_PICKER && params.getMode() == FileChooserParams.MODE_SAVE
            && params.getFilenameHint() != null && !intent.hasExtra(Intent.EXTRA_TITLE)) {
          intent.putExtra(Intent.EXTRA_TITLE, params.getFilenameHint());
        }
        // Saves start in Download (the picker would otherwise open wherever
        // it was last, or on Recent).
        if (Web.SAVE_PICKER && params.getMode() == FileChooserParams.MODE_SAVE
            && !intent.hasExtra(android.provider.DocumentsContract.EXTRA_INITIAL_URI)) {
          intent.putExtra(android.provider.DocumentsContract.EXTRA_INITIAL_URI,
              android.provider.DocumentsContract.buildDocumentUri(
                  "com.android.externalstorage.documents", "primary:Download"));
        }
        startActivityForResult(intent, REQ_FILES);
      } catch (ActivityNotFoundException e) {
        Log.w(TAG, "no app for the file chooser", e);
        pendingFiles = null;
        callback.onReceiveValue(null);
      }
      return true;
    }

    // window.confirm() and alert() as plain app dialogs, without WebView's
    // "The page at https://appassets.androidplatform.net says:" heading.
    @Override
    public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
      new AlertDialog.Builder(MainActivity.this)
          .setMessage(message)
          .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
          .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
          .setOnCancelListener(d -> result.cancel())
          .show();
      return true;
    }

    @Override
    public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
      new AlertDialog.Builder(MainActivity.this)
          .setMessage(message)
          .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
          .setOnCancelListener(d -> result.confirm())
          .show();
      return true;
    }

    @Override
    public void onPermissionRequest(PermissionRequest request) {
      request.deny(); // no camera or microphone
    }

    @Override
    public void onReceivedTitle(WebView view, String title) {
      if (title != null && !title.isEmpty() && !title.startsWith("http")) setTitle(title);
    }

    @Override
    public boolean onConsoleMessage(ConsoleMessage message) {
      if (store.devtools()) Log.d(TAG, "console: " + message.message());
      return true;
    }
  }

  @Override
  protected void onActivityResult(int request, int result, Intent data) {
    if (request != REQ_FILES || pendingFiles == null) {
      super.onActivityResult(request, result, data);
      return;
    }
    Uri[] uris = null;
    if (result == RESULT_OK && data != null) {
      ClipData clip = data.getClipData();
      if (clip != null) {
        uris = new Uri[clip.getItemCount()];
        for (int i = 0; i < uris.length; i++) uris[i] = clip.getItemAt(i).getUri();
      } else if (data.getData() != null) {
        uris = new Uri[] {data.getData()};
      }
    }
    pendingFiles.onReceiveValue(uris);
    pendingFiles = null;
  }

  // ---- Window ----

  /** The caption bar takes the color of the canvas. */
  private void applyWindowColors(int color) {
    root.setBackgroundColor(color);
    getWindow().setBackgroundDrawable(new ColorDrawable(color));
    setTaskDescription(new ActivityManager.TaskDescription.Builder()
        .setPrimaryColor(color)
        .setBackgroundColor(color)
        .setStatusBarColor(color)
        .setNavigationBarColor(color)
        .build());
    WindowInsetsController insets = getWindow().getInsetsController();
    if (insets != null) {
      boolean light = Color.luminance(color) > 0.5f;
      int lightBars = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
          | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
          | WindowInsetsController.APPEARANCE_LIGHT_CAPTION_BARS;
      int transparent = WindowInsetsController.APPEARANCE_TRANSPARENT_CAPTION_BAR_BACKGROUND;
      insets.setSystemBarsAppearance((light ? lightBars : 0) | transparent, lightBars | transparent);
    }
  }

  private int dp(int v) {
    return Math.round(v * getResources().getDisplayMetrics().density);
  }

  private void toast(String text) {
    Toast.makeText(this, text, Toast.LENGTH_LONG).show();
  }

  private static String json(String... kv) {
    JSONObject o = new JSONObject();
    try {
      for (int i = 0; i + 1 < kv.length; i += 2) o.put(kv[i], kv[i + 1]);
    } catch (JSONException e) {
      throw new IllegalStateException(e);
    }
    return o.toString();
  }

  // ---- Debug commands, for testing over adb ----

  /**
   * adb shell am broadcast -a com.merrimentlabs.excalibook.DEBUG
   *   -p com.merrimentlabs.excalibook --es cmd ...
   * Only the shell can send these: the receiver requires android.permission.DUMP,
   * which apps can't hold. ./xb wraps them.
   */
  private void registerDebugCommands() {
    debug = new BroadcastReceiver() {
      @Override
      public void onReceive(Context context, Intent intent) {
        String cmd = intent.getStringExtra("cmd");
        if (cmd == null) return;
        switch (cmd) {
          case "devtools" -> {
            boolean on = intent.getBooleanExtra("on", true);
            store.setDevtools(on);
            WebView.setWebContentsDebuggingEnabled(on);
            Log.i(TAG, "devtools " + on);
          }
          case "reload" -> web.reload();
          case "dump" -> Log.i(TAG, "state url=" + web.getUrl() + " title=" + web.getTitle()
              + " pageReady=" + (page != null) + " incoming=" + incoming.size());
          case "crash" -> web.loadUrl("chrome://crash");
          case "bounds" -> {
            // Where the page is on screen, for tools/readme_images.py's crops.
            int[] at = new int[2];
            web.getLocationOnScreen(at);
            Log.i(TAG, "bounds " + at[0] + " " + at[1] + " " + web.getWidth() + " " + web.getHeight());
          }
          case "incoming" -> {
            // A test file, as if opened with Excalibook: saved to Download
            // (the shell can't grant this app another user's files), then offered.
            String name = intent.getStringExtra("name");
            byte[] bytes = android.util.Base64.decode(intent.getStringExtra("b64"), android.util.Base64.DEFAULT);
            onIo(() -> {
              try {
                String ext = name.substring(name.lastIndexOf('.') + 1).toLowerCase(java.util.Locale.ROOT);
                String mime = android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
                Downloads d = new Downloads(MainActivity.this, name, mime, bytes.length);
                d.write(bytes);
                Uri uri = d.finish();
                own.add(uri);
                main.post(() -> {
                  incoming.add(uri);
                  Log.i(TAG, "incoming test file " + name + " -> " + uri);
                  if (page != null) offerIncoming(page);
                });
              } catch (IOException e) {
                Log.w(TAG, "incoming test file failed", e);
              }
            });
          }
          default -> Log.w(TAG, "unknown debug command " + cmd);
        }
      }
    };
    registerReceiver(debug, new IntentFilter(getPackageName() + ".DEBUG"),
        android.Manifest.permission.DUMP, null, Context.RECEIVER_EXPORTED);
  }
}
