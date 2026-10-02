// SPDX-License-Identifier: MIT
package com.merrimentlabs.excalibook;

import android.content.Context;
import android.content.SharedPreferences;

/** The app's few settings, in private storage. */
final class Store {
  /** Excalidraw's light canvas until the page reports its own color. */
  static final int DEFAULT_THEME = 0xFFFFFFFF;

  private final SharedPreferences prefs;

  Store(Context context) {
    prefs = context.getSharedPreferences("excalibook", Context.MODE_PRIVATE);
  }

  /** The canvas's last color, so the window opens in it. */
  int theme() { return prefs.getInt("theme", DEFAULT_THEME); }
  void setTheme(int color) { prefs.edit().putInt("theme", color).apply(); }

  boolean devtools() { return prefs.getBoolean("devtools", false); }
  void setDevtools(boolean on) { prefs.edit().putBoolean("devtools", on).apply(); }
}
