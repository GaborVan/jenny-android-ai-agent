package com.flagdizero.jenny

import android.content.Context
import android.net.Uri
import android.util.Base64
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject

/**
 * Ponte verso la cartella Google Drive scelta dall'utente per la cloud sync,
 * esposto a Python via Chaquopy (``jclass``) — stesso pattern di
 * ClipboardBridge: classe semplice costruita col Context, istanza cachata in
 * ``runtime/drive_sync_bridge.py``.
 *
 * L'URI dell'albero (persistibile, presa con `takePersistableUriPermission`
 * al momento della scelta in MainActivity) vive nelle SharedPreferences
 * `jenny`, chiavi `drive_sync_tree_uri`/`drive_sync_tree_name` — le stesse
 * scritte dal picker `OpenDocumentTree`. Nessuno stato qui dentro: ogni
 * chiamata rilegge le prefs, così una scelta fatta dall'utente mentre il
 * gateway è vivo si vede alla chiamata successiva senza restart.
 *
 * Tutte le operazioni tollerano l'URI mancante: `{"ok":false,"error":"no_folder"}`.
 * Il contenuto dei file viaggia in base64 (Chaquopy passa stringhe, non byte).
 */
class DriveSyncBridge(context: Context) {

    companion object {
        private const val TAG = "DriveSyncBridge"
        private const val PREFS_NAME = "jenny"
        private const val PREF_TREE_URI = "drive_sync_tree_uri"
        private const val PREF_TREE_NAME = "drive_sync_tree_name"
        private const val MAX_FILE_BYTES = 5 * 1024 * 1024
    }

    private val appContext = context.applicationContext

    private fun treeUri(): Uri? {
        val raw = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_TREE_URI, null) ?: return null
        return try {
            Uri.parse(raw)
        } catch (e: Exception) {
            Log.w(TAG, "stored tree uri is unparsable (${e.javaClass.simpleName})")
            null
        }
    }

    private fun treeName(): String? =
        appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_TREE_NAME, null)

    private fun rootDocument(): DocumentFile? {
        val uri = treeUri() ?: return null
        return try {
            DocumentFile.fromTreeUri(appContext, uri)?.takeIf { it.isDirectory && it.canRead() }
        } catch (e: Exception) {
            Log.w(TAG, "fromTreeUri failed (${e.javaClass.simpleName})")
            null
        }
    }

    private fun noFolder(): String = """{"ok":false,"error":"no_folder"}"""

    private fun notFound(): String = """{"ok":false,"error":"not_found"}"""

    private fun tooLarge(): String = """{"ok":false,"error":"too_large"}"""

    private fun readDocument(doc: DocumentFile): String {
        if (doc.length() > MAX_FILE_BYTES) {
            return tooLarge()
        }
        return try {
            val bytes = appContext.contentResolver.openInputStream(doc.uri)?.use { it.readBytes() }
                ?: return """{"ok":false,"error":"read_failed"}"""
            if (bytes.size > MAX_FILE_BYTES) {
                return tooLarge()
            }
            JSONObject()
                .put("ok", true)
                .put("content", Base64.encodeToString(bytes, Base64.NO_WRAP))
                .toString()
        } catch (e: Exception) {
            Log.w(TAG, "read failed (${e.javaClass.simpleName})")
            """{"ok":false,"error":"read_failed"}"""
        }
    }

    private fun writeDocument(existing: DocumentFile?, parent: DocumentFile, name: String, bytes: ByteArray): String {
        return try {
            val doc = existing ?: parent.createFile("application/octet-stream", name)
                ?: return """{"ok":false,"error":"create_failed"}"""
            appContext.contentResolver.openOutputStream(doc.uri, "wt")?.use { it.write(bytes) }
                ?: return """{"ok":false,"error":"write_failed"}"""
            """{"ok":true}"""
        } catch (e: Exception) {
            Log.w(TAG, "write($name) failed (${e.javaClass.simpleName})")
            """{"ok":false,"error":"write_failed"}"""
        }
    }

    private fun decodeBytes(contentB64: String): ByteArray? {
        return try {
            Base64.decode(contentB64, Base64.NO_WRAP)
        } catch (e: Exception) {
            null
        }
    }
    fun getFolderInfo(): String {
        val root = rootDocument() ?: return noFolder()
        return JSONObject()
            .put("ok", true)
            .put("name", treeName() ?: root.name ?: "")
            .put("uri", root.uri.toString())
            .toString()
    }

    fun listFiles(): String {
        val root = rootDocument() ?: return noFolder()
        val files = JSONArray()
        try {
            for (child in root.listFiles()) {
                if (!child.isFile) continue
                val name = child.name ?: continue
                files.put(
                    JSONObject()
                        .put("name", name)
                        // epoch seconds float, non ms: lo stesso formato dell'algoritmo
                        // di sync lato Python (drive_sync_algorithm.FileMeta.mtime).
                        .put("mtime", child.lastModified() / 1000.0)
                        .put("size", child.length())
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "listFiles failed (${e.javaClass.simpleName})")
            return """{"ok":false,"error":"list_failed"}"""
        }
        return JSONObject().put("ok", true).put("files", files).toString()
    }

    private fun findChild(root: DocumentFile, name: String): DocumentFile? =
        try {
            root.findFile(name)?.takeIf { it.isFile }
        } catch (e: Exception) {
            Log.w(TAG, "findFile($name) failed (${e.javaClass.simpleName})")
            null
        }

    fun readFile(name: String): String {
        val root = rootDocument() ?: return noFolder()
        val doc = findChild(root, name)
            ?: return """{"ok":false,"error":"not_found"}"""
        return readDocument(doc)
    }

    fun writeFile(name: String, contentB64: String): String {
        val root = rootDocument() ?: return noFolder()
        val bytes = decodeBytes(contentB64)
            ?: return """{"ok":false,"error":"invalid_content"}"""
        if (bytes.size > MAX_FILE_BYTES) {
            return tooLarge()
        }
        return writeDocument(findChild(root, name), root, name, bytes)
    }

    fun deleteFile(name: String): String {
        val root = rootDocument() ?: return noFolder()
        val doc = findChild(root, name) ?: return """{"ok":true}""" // già assente: idempotente
        return try {
            if (doc.delete()) """{"ok":true}""" else """{"ok":false,"error":"delete_failed"}"""
        } catch (e: Exception) {
            Log.w(TAG, "deleteFile($name) failed (${e.javaClass.simpleName})")
            """{"ok":false,"error":"delete_failed"}"""
        }
    }

    // ── Operazioni nelle sottocartelle (scope condiviso "Apex-Pamyat") ──
    //
    // La cartella Drive condivisa contiene sottocartelle reali
    // (profile/knowledge/notes): queste chiamate lavorano *dentro* una di
    // esse. Stessi contratti JSON delle omologhe alla radice; una sottocartella
    // inesistente si segnala con ``not_found`` (listFilesIn/readFileIn), mentre
    // deleteFileIn è idempotente come deleteFile. ensureFolder è l'unico punto
    // che crea una sottocartella, e va chiamato dall'orchestratore solo prima
    // del primo write in quella cartella.

    private fun folderChild(root: DocumentFile, folder: String): DocumentFile? =
        try {
            root.findFile(folder)?.takeIf { it.isDirectory }
        } catch (e: Exception) {
            Log.w(TAG, "findFolder($folder) failed (${e.javaClass.simpleName})")
            null
        }

    private fun findChildIn(folder: DocumentFile, name: String): DocumentFile? =
        try {
            folder.findFile(name)?.takeIf { it.isFile }
        } catch (e: Exception) {
            Log.w(TAG, "findFileIn(${folder.name}/$name) failed (${e.javaClass.simpleName})")
            null
        }

    fun ensureFolder(folder: String): String {
        val root = rootDocument() ?: return noFolder()
        return try {
            val existing = root.findFile(folder)
            if (existing != null && existing.isDirectory) return """{"ok":true}"""
            val created = existing ?: root.createDirectory(folder)
            if (created != null && created.isDirectory) """{"ok":true}"""
            else """{"ok":false,"error":"create_failed"}"""
        } catch (e: Exception) {
            Log.w(TAG, "ensureFolder($folder) failed (${e.javaClass.simpleName})")
            """{"ok":false,"error":"create_failed"}"""
        }
    }

    fun listFilesIn(folder: String): String {
        val root = rootDocument() ?: return noFolder()
        val dir = folderChild(root, folder) ?: return notFound()
        val files = JSONArray()
        try {
            for (child in dir.listFiles()) {
                if (!child.isFile) continue
                val name = child.name ?: continue
                files.put(
                    JSONObject()
                        .put("name", name)
                        .put("mtime", child.lastModified() / 1000.0)
                        .put("size", child.length())
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "listFilesIn($folder) failed (${e.javaClass.simpleName})")
            return """{"ok":false,"error":"list_failed"}"""
        }
        return JSONObject().put("ok", true).put("files", files).toString()
    }

    fun readFileIn(folder: String, name: String): String {
        val root = rootDocument() ?: return noFolder()
        val dir = folderChild(root, folder) ?: return notFound()
        val doc = findChildIn(dir, name) ?: return notFound()
        return readDocument(doc)
    }

    fun writeFileIn(folder: String, name: String, contentB64: String): String {
        val root = rootDocument() ?: return noFolder()
        val dir = folderChild(root, folder) ?: return notFound() // ensureFolder prima del primo write
        val bytes = decodeBytes(contentB64)
            ?: return """{"ok":false,"error":"invalid_content"}"""
        if (bytes.size > MAX_FILE_BYTES) {
            return tooLarge()
        }
        return writeDocument(findChildIn(dir, name), dir, name, bytes)
    }

    fun deleteFileIn(folder: String, name: String): String {
        val root = rootDocument() ?: return noFolder()
        val dir = folderChild(root, folder) ?: return """{"ok":true}""" // già assente: idempotente
        val doc = findChildIn(dir, name) ?: return """{"ok":true}"""
        return try {
            if (doc.delete()) """{"ok":true}""" else """{"ok":false,"error":"delete_failed"}"""
        } catch (e: Exception) {
            Log.w(TAG, "deleteFileIn($folder/$name) failed (${e.javaClass.simpleName})")
            """{"ok":false,"error":"delete_failed"}"""
        }
    }
}
