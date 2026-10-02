// SPDX-License-Identifier: MIT
// Adapted from PDF Toolbox, copyright (c) 2026 the PDF Toolbox authors, MIT License.
package com.merrimentlabs.excalibook;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.provider.MediaStore;
import java.io.IOException;
import java.io.OutputStream;

/**
 * One file Excalibook saves without a picker, written to Android's Download folder as its bytes
 * arrive from the page (in chunks, so a big file never sits in one message).
 * MediaStore needs no permission for files the app creates.
 */
final class Downloads {
  final String name;
  final long size;
  private final ContentResolver resolver;
  private Uri uri;
  private OutputStream out;
  private long written;

  Downloads(Context context, String name, String mime, long size) throws IOException {
    this.name = name;
    this.size = size;
    resolver = context.getContentResolver();
    ContentValues values = new ContentValues();
    values.put(MediaStore.Downloads.DISPLAY_NAME, name);
    values.put(MediaStore.Downloads.MIME_TYPE, mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
    values.put(MediaStore.Downloads.IS_PENDING, 1);
    uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
    if (uri == null) throw new IOException("MediaStore refused " + name);
    try {
      out = resolver.openOutputStream(uri, "w");
    } catch (IOException | RuntimeException e) {
      abort(); // no pending row left behind
      throw e;
    }
    if (out == null) {
      abort();
      throw new IOException("can't write " + uri);
    }
  }

  void write(byte[] chunk) throws IOException {
    out.write(chunk);
    written += chunk.length;
  }

  /** Publishes the file; returns its content URI. */
  Uri finish() throws IOException {
    try {
      out.close();
      out = null;
      if (size >= 0 && written != size) {
        throw new IOException(name + ": got " + written + " of " + size + " bytes");
      }
      ContentValues values = new ContentValues();
      values.put(MediaStore.Downloads.IS_PENDING, 0);
      if (resolver.update(uri, values, null, null) != 1) throw new IOException("couldn't publish " + name);
      return uri;
    } catch (IOException | RuntimeException e) {
      abort(); // only a published file counts as saved
      throw e;
    }
  }

  void abort() {
    try {
      if (out != null) out.close();
    } catch (IOException | RuntimeException ignored) {
      // deleting it anyway
    }
    try {
      if (uri != null) resolver.delete(uri, null, null);
    } catch (RuntimeException ignored) {
      // the row is gone already, or the volume is
    }
    uri = null;
  }

  /** The name Download/ shows for it: MediaStore adds " (1)" and so on to duplicates. */
  static String displayName(Context context, Uri uri, String fallback) {
    try (android.database.Cursor c = context.getContentResolver().query(uri,
        new String[] {MediaStore.Downloads.DISPLAY_NAME}, null, null, null)) {
      if (c != null && c.moveToFirst()) return c.getString(0);
    } catch (RuntimeException ignored) {
      // fall back
    }
    return fallback;
  }
}
