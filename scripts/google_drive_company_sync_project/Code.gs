const FOLDER_ID = '11R9uAL6vkbWDiGvjOnS8k22NQxxmrAlK';
// Calls-folder (Zoom transcripts) sync removed 2026-06-16 — unused.
const SYNC_TOKEN = 'C-NJb3jZB_PYpOAQZboPjcuL7zauBlOul1IYB6dt0dWslO2Rd70B6am-9teKP4aP';
const TRANSCRIPT_FILE_NAME = 'transcript.txt';

// Near-realtime sync: a 1-minute time-driven trigger checks the watched folder
// and pings the server only when something actually changed. Run
// setupDriveChangeTrigger() once from the editor to create the trigger.
const CHANGE_NOTIFY_URL = 'https://mcp.m4s.ru/google-drive/events/jTL8ej_9dJuzaZAAFwYDV5nTh496n4Mytn-xT2W0Q_872nVX';
const CHANGE_SIGNATURE_PROPERTY = 'companyFolderSignature';
const CHANGE_SIGNATURE_SCHEMA_PROPERTY = 'companyFolderSignatureSchema';
// Bump this whenever the change-detection logic changes. On the next run every
// already-installed trigger sees a schema mismatch, forces one sync, and
// re-baselines — so fixes self-heal without re-running setupDriveChangeTrigger.
const CHANGE_SIGNATURE_SCHEMA = '2';

const MIME_GOOGLE_DOC = 'application/vnd.google-apps.document';
const MIME_GOOGLE_SHEET = 'application/vnd.google-apps.spreadsheet';
const MIME_DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const MIME_DOC = 'application/msword';
const MIME_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const MIME_XLS = 'application/vnd.ms-excel';

const SUPPORTED_MIME_TYPES = [
  MIME_GOOGLE_DOC,
  MIME_GOOGLE_SHEET,
  MIME_DOCX,
  MIME_DOC,
  MIME_XLSX,
  MIME_XLS,
];

// For DOC/DOCX/XLS/XLSX conversion this Apps Script project must also have:
// - Advanced Google Service: Drive API enabled.
// - appsscript.json oauthScopes including https://www.googleapis.com/auth/drive.

function doGet(e) {
  const token = e && e.parameter ? String(e.parameter.token || '') : '';
  if (token !== SYNC_TOKEN) {
    return jsonResponse({ ok: false, error: 'Unauthorized' }, 401);
  }
  if (e && e.parameter && e.parameter.diag === '1') {
    const props = PropertiesService.getScriptProperties();
    return jsonResponse({
      ok: true,
      diag: true,
      now: new Date().toISOString(),
      last_trigger_run: props.getProperty('lastTriggerRun') || null,
      last_trigger_result: props.getProperty('lastTriggerResult') || null,
      last_notify_detail: props.getProperty('lastNotifyDetail') || null,
      signature_schema: props.getProperty(CHANGE_SIGNATURE_SCHEMA_PROPERTY) || null,
      notify_url_set: Boolean(CHANGE_NOTIFY_URL),
    });
  }
  return buildCompanySyncResponse({});
}

function doPost(e) {
  let payload = {};
  try {
    payload = e && e.postData && e.postData.contents
      ? JSON.parse(e.postData.contents)
      : {};
  } catch (error) {
    return jsonResponse({ ok: false, error: 'Invalid JSON payload' }, 400);
  }

  const token = String(payload.token || '');
  if (token !== SYNC_TOKEN) {
    return jsonResponse({ ok: false, error: 'Unauthorized' }, 401);
  }

  const action = String(payload.action || '');
  if (action === 'sheet_append' || action === 'sheet_update') {
    return handleSheetWrite(action, payload);
  }

  return buildCompanySyncResponse(payload);
}

// Authenticated write into a Google Sheet (runs as the deploying owner, so it can
// edit any spreadsheet the owner can edit). action: 'sheet_append' appends rows to
// the end of a sheet; 'sheet_update' writes values into an A1 range.
function handleSheetWrite(action, payload) {
  try {
    const ssId = String(payload.spreadsheet_id || '');
    if (!ssId) {
      return jsonResponse({ ok: false, error: 'spreadsheet_id required' }, 400);
    }
    const ss = SpreadsheetApp.openById(ssId);
    const sheetName = payload.sheet ? String(payload.sheet) : '';
    let sheet = sheetName ? ss.getSheetByName(sheetName) : ss.getSheets()[0];
    if (!sheet) {
      if (payload.create_sheet && sheetName) {
        sheet = ss.insertSheet(sheetName);
      } else {
        return jsonResponse({ ok: false, error: 'sheet not found: ' + sheetName }, 404);
      }
    }
    if (action === 'sheet_append') {
      const rows = payload.rows || [];
      if (!rows.length || !Array.isArray(rows[0])) {
        return jsonResponse({ ok: false, error: 'rows must be a non-empty list of lists' }, 400);
      }
      const width = Math.max.apply(null, rows.map(function (r) { return r.length; }));
      const norm = rows.map(function (r) {
        const row = r.slice();
        while (row.length < width) { row.push(''); }
        return row;
      });
      const startRow = sheet.getLastRow() + 1;
      sheet.getRange(startRow, 1, norm.length, width).setValues(norm);
      return jsonResponse({ ok: true, spreadsheet_id: ssId, sheet: sheet.getName(), appended_rows: norm.length, start_row: startRow });
    }
    // sheet_update
    const range = String(payload.range || '');
    const values = payload.values || [];
    if (!range || !values.length || !Array.isArray(values[0])) {
      return jsonResponse({ ok: false, error: 'range and values (list of lists) required' }, 400);
    }
    sheet.getRange(range).setValues(values);
    return jsonResponse({ ok: true, spreadsheet_id: ssId, sheet: sheet.getName(), updated_range: range, updated_rows: values.length });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error && error.message ? error.message : error) }, 500);
  }
}

function buildCompanySyncResponse(payload) {
  const knownFiles = payload && payload.known_files ? payload.known_files : {};
  const folder = DriveApp.getFolderById(FOLDER_ID);
  const files = [];
  const folders = [];
  collectFolderTree(folder, [], folders, files);

  const supportedFiles = files.filter((entry) => SUPPORTED_MIME_TYPES.includes(entry.file.getMimeType()));
  const documents = [];
  const document_errors = [];
  supportedFiles.forEach((entry) => {
    const file = entry.file;
    try {
      documents.push(filePayload(file, entry, knownFiles));
    } catch (error) {
      document_errors.push({
        id: file.getId(),
        name: file.getName(),
        mime_type: file.getMimeType(),
        url: file.getUrl(),
        parent_folder_id: entry.parent_folder_id,
        path_parts: entry.path_parts,
        path: entry.path_parts.concat([file.getName()]).join(' / '),
        error: String(error && error.message ? error.message : error),
      });
    }
  });
  const skipped_files = files
    .filter((entry) => !SUPPORTED_MIME_TYPES.includes(entry.file.getMimeType()))
    .map((entry) => ({
      id: entry.file.getId(),
      name: entry.file.getName(),
      mime_type: entry.file.getMimeType(),
      url: entry.file.getUrl(),
      parent_folder_id: entry.parent_folder_id,
      path_parts: entry.path_parts,
      path: entry.path_parts.concat([entry.file.getName()]).join(' / '),
      reason: 'unsupported_mime_type',
    }));

  const transcripts = [];  // calls-folder sync removed 2026-06-16 (unused)

  return jsonResponse({
    ok: true,
    folder_id: FOLDER_ID,
    synced_at: new Date().toISOString(),
    folders,
    documents,
    document_errors,
    skipped_files,
    transcripts,
  });
}

function collectFolderTree(folder, pathParts, folders, files) {
  const fileIterator = folder.getFiles();
  while (fileIterator.hasNext()) {
    files.push({
      file: fileIterator.next(),
      parent_folder_id: folder.getId(),
      parent_folder_name: folder.getName(),
      path_parts: pathParts.slice(),
    });
  }

  const folderIterator = folder.getFolders();
  while (folderIterator.hasNext()) {
    const child = folderIterator.next();
    const childPath = pathParts.concat([child.getName()]);
    folders.push({
      id: child.getId(),
      name: child.getName(),
      url: child.getUrl(),
      parent_folder_id: folder.getId(),
      parent_folder_name: folder.getName(),
      path_parts: childPath,
      path: childPath.join(' / '),
    });
    collectFolderTree(child, childPath, folders, files);
  }
}

function collectFolderFiles(folder, files) {
  const fileIterator = folder.getFiles();
  while (fileIterator.hasNext()) {
    files.push({
      file: fileIterator.next(),
      parent_folder_id: folder.getId(),
      parent_folder_name: folder.getName(),
      path_parts: [],
    });
  }

  const folderIterator = folder.getFolders();
  while (folderIterator.hasNext()) {
    collectFolderFiles(folderIterator.next(), files);
  }
}

function legacySkippedFilePayload(file) {
  return {
      id: file.getId(),
      name: file.getName(),
      mime_type: file.getMimeType(),
      url: file.getUrl(),
      reason: 'unsupported_mime_type',
  };
}

function collectTranscriptFiles(folder, pathParts, transcripts) {
  const currentPath = pathParts.concat([folder.getName()]);
  const fileIterator = folder.getFiles();

  while (fileIterator.hasNext()) {
    const file = fileIterator.next();
    if (file.getName().toLowerCase() !== TRANSCRIPT_FILE_NAME) {
      continue;
    }

    transcripts.push({
      id: file.getId(),
      name: file.getName(),
      url: file.getUrl(),
      mime_type: file.getMimeType(),
      updated_at: file.getLastUpdated().toISOString(),
      parent_folder_id: folder.getId(),
      parent_folder_name: folder.getName(),
      path_parts: currentPath,
      path: currentPath.join(' / '),
      content: file.getBlob().getDataAsString('UTF-8'),
    });
  }

  const folderIterator = folder.getFolders();
  while (folderIterator.hasNext()) {
    collectTranscriptFiles(folderIterator.next(), currentPath, transcripts);
  }
}

function filePayload(file, entry, knownFiles) {
  const mimeType = file.getMimeType();
  const updatedAt = file.getLastUpdated().toISOString();
  const knownFile = knownFiles && knownFiles[file.getId()] ? knownFiles[file.getId()] : null;
  const pathParts = entry && entry.path_parts ? entry.path_parts : [];
  const parentFolderId = entry && entry.parent_folder_id ? entry.parent_folder_id : '';
  const parentFolderName = entry && entry.parent_folder_name ? entry.parent_folder_name : '';
  const fullPath = pathParts.concat([file.getName()]).join(' / ');
  const unchanged = knownFile
    && String(knownFile.updated_at || '') === updatedAt
    && String(knownFile.name || '') === file.getName()
    && String(knownFile.parent_folder_id || '') === parentFolderId
    && String(knownFile.path || '') === fullPath;

  if (unchanged) {
    return {
      id: file.getId(),
      name: file.getName(),
      mime_type: mimeType,
      url: file.getUrl(),
      updated_at: updatedAt,
      parent_folder_id: parentFolderId,
      parent_folder_name: parentFolderName,
      path_parts: pathParts,
      path: fullPath,
      unchanged: true,
    };
  }

  let content = '';
  let contentFormat = 'text';
  let blocks = [];
  let convertedMimeType = null;

  if (mimeType === MIME_GOOGLE_DOC) {
    const parsed = googleDocContent(file.getId());
    content = parsed.content;
    blocks = parsed.blocks;
  } else if (mimeType === MIME_GOOGLE_SHEET) {
    const parsed = googleSheetContent(file.getId());
    content = parsed.content;
    blocks = parsed.blocks;
    contentFormat = 'markdown';
  } else if ([MIME_DOCX, MIME_DOC].includes(mimeType)) {
    const parsed = convertedGoogleDocContent(file);
    content = parsed.content;
    blocks = parsed.blocks;
    convertedMimeType = MIME_GOOGLE_DOC;
  } else if ([MIME_XLSX, MIME_XLS].includes(mimeType)) {
    const parsed = convertedGoogleSheetContent(file);
    content = parsed.content;
    blocks = parsed.blocks;
    contentFormat = 'markdown';
    convertedMimeType = MIME_GOOGLE_SHEET;
  }

  return {
    id: file.getId(),
    name: file.getName(),
    mime_type: mimeType,
    url: file.getUrl(),
    updated_at: updatedAt,
    parent_folder_id: parentFolderId,
    parent_folder_name: parentFolderName,
    path_parts: pathParts,
    path: fullPath,
    content_format: contentFormat,
    converted_mime_type: convertedMimeType,
    content,
    blocks,
  };
}

function convertedGoogleDocContent(file) {
  const convertedFileId = temporaryConvertedFileId(file, MIME_GOOGLE_DOC);
  try {
    return googleDocContent(convertedFileId);
  } finally {
    trashTemporaryFile(convertedFileId);
  }
}

function convertedGoogleSheetContent(file) {
  const convertedFileId = temporaryConvertedFileId(file, MIME_GOOGLE_SHEET);
  try {
    return googleSheetContent(convertedFileId);
  } finally {
    trashTemporaryFile(convertedFileId);
  }
}

function temporaryConvertedFileId(file, targetMimeType) {
  if (typeof Drive === 'undefined' || !Drive.Files || !Drive.Files.copy) {
    throw new Error('Advanced Drive service is required: enable Services -> Drive API in Apps Script.');
  }

  const resource = {
    title: '__sync_tmp__' + file.getName(),
    mimeType: targetMimeType,
  };

  // Drive.Files.copy can hit transient "User rate limit exceeded" / backend
  // errors when many documents are converted in one run. Retry with backoff so
  // a single full sync does not drop documents because of a momentary limit.
  let lastError = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const converted = Drive.Files.copy(resource, file.getId(), { convert: true });
      if (converted && converted.id) {
        return converted.id;
      }
      lastError = new Error('Failed to convert file: ' + file.getName());
    } catch (error) {
      lastError = error;
      const message = String(error && error.message ? error.message : error);
      const transient = /rate limit|ratelimit|userratelimitexceeded|backenderror|internal error|try again|temporar/i.test(message);
      if (!transient) {
        throw error;
      }
    }
    Utilities.sleep(2000 * (attempt + 1));
  }
  throw lastError || new Error('Failed to convert file: ' + file.getName());
}

function trashTemporaryFile(fileId) {
  try {
    DriveApp.getFileById(fileId).setTrashed(true);
  } catch (error) {
    // Best effort cleanup. Sync should not fail only because temp cleanup failed.
  }
}

function googleDocContent(fileId) {
  const doc = DocumentApp.openById(fileId);
  const blocks = googleDocBodyBlocks(doc.getBody());
  return {
    blocks,
    content: blocksToText(blocks),
  };
}

function googleDocBodyBlocks(body) {
  const blocks = [];
  let tableIndex = 1;

  for (let i = 0; i < body.getNumChildren(); i += 1) {
    const element = body.getChild(i);
    const type = element.getType();

    if (type === DocumentApp.ElementType.PARAGRAPH) {
      const text = element.asParagraph().getText().trim();
      if (text) blocks.push({ type: 'paragraph', text });
      continue;
    }

    if (type === DocumentApp.ElementType.LIST_ITEM) {
      const text = element.asListItem().getText().trim();
      if (text) blocks.push({ type: 'list_item', text });
      continue;
    }

    if (type === DocumentApp.ElementType.TABLE) {
      const tableBlock = googleDocTableBlock(element.asTable(), tableIndex);
      if (tableBlock) {
        blocks.push(tableBlock);
        tableIndex += 1;
      }
      continue;
    }

    const fallback = element.getText ? String(element.getText()).trim() : '';
    if (fallback) blocks.push({ type: 'paragraph', text: fallback });
  }

  return blocks;
}

function googleDocTableBlock(table, tableIndex) {
  const rows = [];
  for (let rowIndex = 0; rowIndex < table.getNumRows(); rowIndex += 1) {
    const row = table.getRow(rowIndex);
    const values = [];
    for (let cellIndex = 0; cellIndex < row.getNumCells(); cellIndex += 1) {
      values.push(markdownCell(row.getCell(cellIndex).getText()));
    }
    rows.push(values);
  }

  if (!rows.length) return null;
  return tableBlockFromRows('Таблица ' + tableIndex, rows);
}

function googleSheetContent(fileId) {
  const spreadsheet = SpreadsheetApp.openById(fileId);
  const blocks = [];

  spreadsheet.getSheets().forEach((sheet) => {
    const values = sheet.getDataRange().getDisplayValues();
    blocks.push({ type: 'heading', text: sheet.getName(), level: 1 });

    if (!values.length) {
      return;
    }

    const normalized = normalizeRows(values);
    blocks.push(tableBlockFromRows(sheet.getName(), normalized));
  });

  return {
    blocks,
    content: blocksToText(blocks),
  };
}

function normalizeRows(values) {
  const width = values.reduce((max, row) => Math.max(max, row.length), 0);
  return values.map((row) => {
    const copy = row.slice();
    while (copy.length < width) copy.push('');
    return copy.map(markdownCell);
  });
}

function rowsToMarkdownTable(rows) {
  const normalized = normalizeRows(rows);
  if (!normalized.length) return '';
  const header = normalized[0].map((value, index) => value || 'Колонка ' + (index + 1));
  const output = [];
  output.push('| ' + header.join(' | ') + ' |');
  output.push('| ' + header.map(() => '---').join(' | ') + ' |');
  normalized.slice(1).forEach((row) => {
    output.push('| ' + row.join(' | ') + ' |');
  });
  return output.join('\n');
}

function rowsToRecordText(rows) {
  const normalized = normalizeRows(rows);
  if (normalized.length < 2) return '';
  const headers = normalized[0].map((value, index) => value || 'Колонка ' + (index + 1));
  const records = [];

  normalized.slice(1).forEach((row, rowIndex) => {
    const pairs = [];
    row.forEach((value, index) => {
      if (value) pairs.push('- ' + headers[index] + ': ' + value);
    });
    if (pairs.length) {
      records.push('Запись ' + (rowIndex + 1) + ':\n' + pairs.join('\n'));
    }
  });

  return records.join('\n\n');
}

function tableBlockFromRows(title, rows) {
  const normalized = normalizeRows(rows);
  const headers = normalized[0].map((value, index) => value || 'Колонка ' + (index + 1));
  const dataRows = normalized.slice(1);
  const records = dataRows
    .map((row) => {
      const record = {};
      row.forEach((value, index) => {
        if (value) record[headers[index]] = value;
      });
      return record;
    })
    .filter((record) => Object.keys(record).length > 0);

  return {
    type: 'table',
    title,
    headers,
    rows: dataRows,
    records,
    markdown: rowsToMarkdownTable(normalized),
  };
}

function blocksToText(blocks) {
  const chunks = [];
  blocks.forEach((block) => {
    if (block.type === 'heading') {
      chunks.push('# ' + block.text);
      return;
    }
    if (block.type === 'paragraph') {
      chunks.push(block.text);
      return;
    }
    if (block.type === 'list_item') {
      chunks.push('- ' + block.text);
      return;
    }
    if (block.type === 'table') {
      chunks.push(block.title || 'Таблица');
      chunks.push(block.markdown || rowsToMarkdownTable([block.headers].concat(block.rows || [])));
      if (block.records && block.records.length) {
        chunks.push(
          block.records
            .map((record, index) => {
              const lines = Object.keys(record).map((key) => '- ' + key + ': ' + record[key]);
              return 'Запись ' + (index + 1) + ':\n' + lines.join('\n');
            })
            .join('\n\n')
        );
      }
    }
  });
  return chunks.join('\n\n');
}

function markdownCell(value) {
  return String(value || '')
    .replace(/\r?\n/g, '<br>')
    .replace(/\|/g, '\\|')
    .trim();
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---------------------------------------------------------------------------
// Near-realtime change detection
// ---------------------------------------------------------------------------

// Run this ONCE from the Apps Script editor (Run -> setupDriveChangeTrigger).
// It authorizes the script and installs a time-driven trigger that fires every
// minute. Safe to run again; it removes duplicate triggers first.
function setupDriveChangeTrigger() {
  removeDriveChangeTriggers();
  ScriptApp.newTrigger('checkDriveChangesAndNotify')
    .timeBased()
    .everyMinutes(1)
    .create();
  // Do NOT seed a baseline. The first trigger run will sync the current state,
  // so any edits made before setup are picked up instead of being swallowed.
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(CHANGE_SIGNATURE_PROPERTY);
  props.deleteProperty(CHANGE_SIGNATURE_SCHEMA_PROPERTY);
}

function removeDriveChangeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'checkDriveChangesAndNotify') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

// Trigger handler: compares a lightweight signature of the watched folder tree
// (file/folder ids + last-updated times + names + parents, no file content)
// against the previous run and pings the server only when it changed.
function checkDriveChangesAndNotify() {
  const props = PropertiesService.getScriptProperties();
  props.setProperty('lastTriggerRun', new Date().toISOString());
  const previous = props.getProperty(CHANGE_SIGNATURE_PROPERTY) || '';
  const previousSchema = props.getProperty(CHANGE_SIGNATURE_SCHEMA_PROPERTY) || '';
  const current = computeFolderSignature();
  // Skip only when nothing changed AND the baseline was written by the current
  // detection logic. A schema mismatch forces one sync so a stale/old baseline
  // can never silently swallow an un-synced change.
  if (previousSchema === CHANGE_SIGNATURE_SCHEMA && current === previous) {
    props.setProperty('lastTriggerResult', 'no_change');
    return;
  }
  // Persist the new signature only after the server confirms receipt, so a
  // transient failure is retried on the next minute instead of being lost.
  const ok = notifyServerOfDriveChange();
  props.setProperty('lastTriggerResult', 'notified=' + ok);
  if (ok) {
    props.setProperty(CHANGE_SIGNATURE_PROPERTY, current);
    props.setProperty(CHANGE_SIGNATURE_SCHEMA_PROPERTY, CHANGE_SIGNATURE_SCHEMA);
  }
}

function notifyServerOfDriveChange() {
  const props = PropertiesService.getScriptProperties();
  if (!CHANGE_NOTIFY_URL) {
    props.setProperty('lastNotifyDetail', 'no_url');
    return false;
  }
  try {
    const response = UrlFetchApp.fetch(CHANGE_NOTIFY_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ source: 'drive_change_trigger', at: new Date().toISOString() }),
      muteHttpExceptions: true,
    });
    const code = response.getResponseCode();
    props.setProperty('lastNotifyDetail', 'code=' + code);
    // 2xx = synced. 409 = server busy with another sync; keep old signature and
    // retry next minute. Other codes also retry.
    return code >= 200 && code < 300;
  } catch (error) {
    props.setProperty('lastNotifyDetail', 'error=' + (error && error.message ? error.message : String(error)));
    return false;
  }
}

function computeFolderSignature() {
  const parts = [];
  collectSignature(DriveApp.getFolderById(FOLDER_ID), parts);
  parts.sort();
  const digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.MD5,
    parts.join('|'),
    Utilities.Charset.UTF_8
  );
  return Utilities.base64Encode(digest);
}

function collectSignature(folder, parts) {
  const parentId = folder.getId();
  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();
    parts.push('f:' + file.getId() + ':' + parentId + ':' + file.getLastUpdated().getTime() + ':' + file.getName());
  }
  const folders = folder.getFolders();
  while (folders.hasNext()) {
    const child = folders.next();
    parts.push('d:' + child.getId() + ':' + parentId + ':' + child.getName());
    collectSignature(child, parts);
  }
}
