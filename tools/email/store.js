"use strict";

var fs = require("fs");
var path = require("path");

var dataDir = path.join(__dirname, "data");
var sendsFile = path.join(dataDir, "email_sends.json");
var eventsFile = path.join(dataDir, "email_events.json");

function ensureFiles() {
    if (!fs.existsSync(dataDir)) {
        fs.mkdirSync(dataDir, { recursive: true });
    }

    if (!fs.existsSync(sendsFile)) {
        fs.writeFileSync(sendsFile, "[]", "utf8");
    }

    if (!fs.existsSync(eventsFile)) {
        fs.writeFileSync(eventsFile, "[]", "utf8");
    }
}

function readJson(filePath) {
    ensureFiles();
    var contents = fs.readFileSync(filePath, "utf8");
    try {
        return JSON.parse(contents || "[]");
    } catch (err) {
        return [];
    }
}

function writeJson(filePath, payload) {
    ensureFiles();
    fs.writeFileSync(filePath, JSON.stringify(payload, null, 2));
}

function recordSend(recipient, issueId, messageId, provider) {
    var sends = readJson(sendsFile);
    sends.push({
        recipient: recipient,
        issue_id: issueId,
        message_id: messageId,
        provider: provider,
        sent_at: new Date().toISOString()
    });
    writeJson(sendsFile, sends);
}

function recordEvent(event) {
    var events = readJson(eventsFile);
    events.push({
        issue_id: event.issue_id,
        message_id: event.message_id,
        recipient: event.recipient || event.email,
        type: event.type,
        payload: event.payload || event.meta || {},
        received_at: new Date().toISOString()
    });
    writeJson(eventsFile, events);
}

function listSends() {
    return readJson(sendsFile);
}

function listEvents() {
    return readJson(eventsFile);
}

module.exports = {
    recordSend: recordSend,
    recordEvent: recordEvent,
    listSends: listSends,
    listEvents: listEvents
};
