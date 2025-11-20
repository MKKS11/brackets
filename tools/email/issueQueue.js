"use strict";

var fs = require("fs");
var path = require("path");

var queueFile = path.join(__dirname, "data", "issue_queue.json");

function ensureQueueFile() {
    var dir = path.dirname(queueFile);
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
    if (!fs.existsSync(queueFile)) {
        fs.writeFileSync(queueFile, "[]", "utf8");
    }
}

function loadQueue() {
    ensureQueueFile();
    var raw = fs.readFileSync(queueFile, "utf8");
    try {
        return JSON.parse(raw || "[]");
    } catch (err) {
        return [];
    }
}

function writeQueue(queue) {
    ensureQueueFile();
    fs.writeFileSync(queueFile, JSON.stringify(queue, null, 2));
}

function getIssuesReady(currentDate) {
    var now = currentDate || new Date();
    var queue = loadQueue();
    return queue.filter(function filter(issue) {
        if (issue.sent_at) {
            return false;
        }
        if (!issue.send_at) {
            return true;
        }
        return new Date(issue.send_at).getTime() <= now.getTime();
    });
}

function markSent(issueId) {
    var queue = loadQueue();
    var updated = queue.map(function map(issue) {
        if (issue.id === issueId) {
            issue.sent_at = new Date().toISOString();
        }
        return issue;
    });
    writeQueue(updated);
}

module.exports = {
    loadQueue: loadQueue,
    writeQueue: writeQueue,
    getIssuesReady: getIssuesReady,
    markSent: markSent
};
