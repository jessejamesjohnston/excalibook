// SPDX-License-Identifier: MIT
// Adapted from PDF Toolbox, copyright (c) 2026 the PDF Toolbox authors, MIT License.
package com.merrimentlabs.excalibook;

import android.content.Context;
import android.content.res.AssetManager;
import android.net.Uri;
import android.os.Build;
import android.util.Log;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import java.io.ByteArrayInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Excalidraw's page, served from the APK's assets (assets/web) on a private
 * https origin, and its message channel.
 */
final class Web {
  static final String TAG = "Excalibook";
  static final String HOST = "appassets.androidplatform.net";
  static final String ORIGIN = "https://" + HOST;
  static final String HOME = ORIGIN + "/";
  private static final String ROOT = "web";

  interface Listener {
    void onMessage(WebMessageCompat message, JavaScriptReplyProxy reply);
  }

  private Web() {}

  static void configure(WebView web, Listener listener) {
    WebSettings s = web.getSettings();
    s.setJavaScriptEnabled(true);
    s.setDomStorageEnabled(true);
    s.setAllowFileAccess(false);
    s.setAllowContentAccess(false);
    s.setSupportZoom(false);
    s.setBuiltInZoomControls(false);
    s.setTextZoom(100); // the system font size would otherwise scale only the UI text
    s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    s.setSupportMultipleWindows(false);
    if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
      WebSettingsCompat.setAlgorithmicDarkeningAllowed(s, false); // Excalidraw has its own dark theme
    }
    WebViewCompat.addWebMessageListener(web, "excalibook", Set.of(ORIGIN),
        (view, message, source, mainFrame, reply) -> listener.onMessage(message, reply));
    if (!SAVE_PICKER && WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
      WebViewCompat.addDocumentStartJavaScript(web, HIDE_FILE_SYSTEM_ACCESS, Set.of(ORIGIN));
    }
  }

  /**
   * Android 17 (API 37) gave WebView's file chooser a save mode, which is what
   * File System Access (showSaveFilePicker) needs. Before that WebView still
   * offers the API, but every save picker fails at once with AbortError. Hide
   * it there, so Excalidraw opens with a plain file input and saves through
   * the download fallback (web/src/android.ts) to Download/.
   */
  static final boolean SAVE_PICKER = Build.VERSION.SDK_INT >= 37;

  private static final String HIDE_FILE_SYSTEM_ACCESS =
      "for (const k of ['showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker']) {"
      + " try { delete window[k]; } catch (e) {}"
      + " if (k in window) { try { delete Window.prototype[k]; } catch (e) {} } }";

  static boolean isApp(Uri uri) {
    return uri != null && "https".equals(uri.getScheme()) && HOST.equals(uri.getHost());
  }

  /** The page's files: the path as is, a directory's index.html, else 404. */
  static WebResourceResponse serve(Context context, Uri url) {
    String path = url.getPath();
    if (path == null || path.isEmpty()) path = "/";
    if (path.contains("..")) return notFound(path);
    String file = path.endsWith("/") ? path + "index.html" : path;
    try {
      InputStream in = context.getAssets().open(ROOT + file, AssetManager.ACCESS_STREAMING);
      return respond(200, "OK", mime(file), in);
    } catch (FileNotFoundException e) {
      return notFound(path);
    } catch (IOException e) {
      Log.w(TAG, "can't read " + file, e);
      return notFound(path);
    }
  }

  /**
   * Anything the page asks for outside the app. There is no INTERNET
   * permission, so it couldn't load anyway; answering here keeps it from
   * even trying, and the log line shows what asked (tools/smoke.py checks
   * there are none).
   */
  static WebResourceResponse blocked(Uri url) {
    Log.w(TAG, "blocked network request: " + url);
    return respond(403, "Forbidden", "text/plain",
        new ByteArrayInputStream("Excalibook works offline".getBytes(StandardCharsets.UTF_8)));
  }

  private static WebResourceResponse notFound(String path) {
    Log.w(TAG, "not in the app: " + path);
    return respond(404, "Not Found", "text/plain",
        new ByteArrayInputStream("Not found".getBytes(StandardCharsets.UTF_8)));
  }

  private static WebResourceResponse respond(int status, String reason, String mime, InputStream in) {
    Map<String, String> headers = new HashMap<>();
    headers.put("X-Content-Type-Options", "nosniff");
    headers.put("Cache-Control", "no-cache");
    // Fonts are fetched by Excalidraw's export code (to embed them in SVGs).
    headers.put("Access-Control-Allow-Origin", ORIGIN);
    boolean text = mime.startsWith("text/") || mime.endsWith("javascript") || mime.endsWith("json")
        || mime.endsWith("xml");
    return new WebResourceResponse(mime, text ? "utf-8" : null, status, reason, headers, in);
  }

  private static final Map<String, String> TYPES = Map.ofEntries(
      Map.entry("html", "text/html"), Map.entry("js", "text/javascript"), Map.entry("mjs", "text/javascript"),
      Map.entry("css", "text/css"), Map.entry("json", "application/json"),
      Map.entry("svg", "image/svg+xml"), Map.entry("png", "image/png"), Map.entry("ico", "image/x-icon"),
      Map.entry("woff", "font/woff"), Map.entry("woff2", "font/woff2"), Map.entry("ttf", "font/ttf"),
      Map.entry("txt", "text/plain"), Map.entry("wasm", "application/wasm"));

  static String mime(String path) {
    String name = path.substring(path.lastIndexOf('/') + 1);
    int dot = name.lastIndexOf('.');
    String ext = dot < 0 ? "" : name.substring(dot + 1).toLowerCase(Locale.ROOT);
    String type = TYPES.get(ext);
    return type != null ? type : "application/octet-stream";
  }
}
