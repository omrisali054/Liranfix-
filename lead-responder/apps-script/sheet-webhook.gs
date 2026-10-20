/**
 * Lead Responder → Google Sheet. Every lead becomes a row; status changes update the same row.
 *
 * Setup (3 minutes, in the CLIENT's Google account so the sheet is theirs):
 *  1. sheets.new → Extensions → Apps Script → paste this file over the empty one.
 *  2. Change SECRET below to a long random string. Save.
 *  3. Deploy → New deployment → type "Web app" → Execute as: Me, Who has access: Anyone → Deploy
 *     (approve the permissions). Copy the URL that ends with /exec.
 *  4. In clients/<name>.toml:
 *       [sheets]
 *       url = "<the /exec URL>"
 *       secret = "<the same SECRET>"
 *  After editing this script later: Deploy → Manage deployments → edit → Version: New version.
 */
const SECRET = 'CHANGE-ME';
const SHEET_NAME = 'לידים';
// Must match sheet_row() in lead_responder/messages.py
const COLUMNS = ['מזהה', 'נוצר', 'שם', 'טלפון', 'אימייל', 'מקור', 'סטטוס', 'עודכן', 'ענה בוואטסאפ', 'וואטסאפ', 'פרטים'];

function doPost(e) {
  let data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply({ ok: false, error: 'bad json' });
  }
  if (SECRET === 'CHANGE-ME' || data.secret !== SECRET) {
    return reply({ ok: false, error: 'bad secret' });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sheet = getSheet();
    const row = COLUMNS.map(function (name) {
      const value = data.row[name];
      return value === undefined || value === null ? '' : value;
    });
    // Phones like 050-1234567 must stay text, not become numbers / formulas.
    const phone = COLUMNS.indexOf('טלפון');
    if (row[phone]) row[phone] = "'" + row[phone];
    const found = findRow(sheet, String(data.row['מזהה']));
    if (found) {
      sheet.getRange(found, 1, 1, row.length).setValues([row]);
    } else {
      sheet.appendRow(row);
    }
    return reply({ ok: true });
  } finally {
    lock.releaseLock();
  }
}

function getSheet() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = book.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = book.insertSheet(SHEET_NAME);
    sheet.appendRow(COLUMNS);
    sheet.setFrozenRows(1);
    sheet.setRightToLeft(true);
    sheet.getRange(1, 1, 1, COLUMNS.length).setFontWeight('bold');
  }
  return sheet;
}

function findRow(sheet, id) {
  const last = sheet.getLastRow();
  if (last < 2) return 0;
  const ids = sheet.getRange(2, 1, last - 1, 1).getValues();
  for (let i = ids.length - 1; i >= 0; i--) {  // newest rows are at the bottom
    if (String(ids[i][0]) === id) return i + 2;
  }
  return 0;
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
