const CALLS_FOLDER_ID = '11cy4HOs1IPOgrIsI9cVDZMnZ5i4UYgCG';
function syncToken_() {
  return PropertiesService.getScriptProperties().getProperty('SYNC_TOKEN') || '';
}
const TRANSCRIPT_FILE_NAME = 'transcript.txt';

function doGet(e) {
  const token = e && e.parameter ? String(e.parameter.token || '') : '';
  if (!syncToken_() || token !== syncToken_()) {
    return jsonResponse({ ok: false, error: 'Unauthorized' });
  }

  const folder = DriveApp.getFolderById(CALLS_FOLDER_ID);
  const transcripts = [];
  collectTranscriptFiles(folder, [], transcripts);

  return jsonResponse({
    ok: true,
    folder_id: CALLS_FOLDER_ID,
    synced_at: new Date().toISOString(),
    transcripts,
  });
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

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
