"use strict";

var http = require("http");
var issueQueue = require("./issueQueue");
var provider = require("./provider");
var store = require("./store");
var content = require("./content");

var DEFAULT_PIXEL = process.env.TRACKING_PIXEL_BASE || "https://example.com/pixel.gif";
var DEFAULT_UTM = {
    utm_source: process.env.UTM_SOURCE || "newsletter",
    utm_medium: process.env.UTM_MEDIUM || "email",
    utm_campaign: process.env.UTM_CAMPAIGN || "scheduled-send"
};

function getEasternDate(date) {
    return new Date(date.toLocaleString("en-US", { timeZone: "America/New_York" }));
}

function toUtcFromEastern(etDate) {
    var now = new Date();
    var nowEt = getEasternDate(now);
    var offset = now.getTime() - nowEt.getTime();
    return new Date(etDate.getTime() + offset);
}

function millisUntilNextSend() {
    var now = new Date();
    var nowEt = getEasternDate(now);
    var targetEt = new Date(nowEt);
    targetEt.setHours(5, 25, 0, 0);
    if (targetEt.getTime() <= nowEt.getTime()) {
        targetEt.setDate(targetEt.getDate() + 1);
    }
    var targetUtc = toUtcFromEastern(targetEt);
    return Math.max(targetUtc.getTime() - now.getTime(), 1000 * 60);
}

function decorateHtml(html, utmParams, pixelBaseUrl, messageId, issueId) {
    var withUtm = content.appendUtmParameters(html, utmParams);
    return content.injectOpenTrackingPixel(withUtm, pixelBaseUrl, messageId, issueId);
}

function sendIssue(issue, callback) {
    if (!issue.recipients || !issue.recipients.length) {
        return callback();
    }

    var remaining = issue.recipients.length;
    var errors = [];

    issue.recipients.forEach(function sendTo(recipient) {
        var messageId = provider.createMessageId();
        var utm = Object.assign({}, DEFAULT_UTM, issue.utm || {}, { utm_content: issue.id });
        var html = decorateHtml(issue.html, utm, DEFAULT_PIXEL, messageId, issue.id);

        provider.sendEmail({
            to: recipient,
            issueId: issue.id,
            subject: issue.subject,
            html: html,
            messageId: messageId
        }, function onSend(err) {
            remaining -= 1;
            if (err) {
                errors.push({ recipient: recipient, error: err });
            }
            if (remaining === 0) {
                if (errors.length) {
                    return callback(errors);
                }
                issueQueue.markSent(issue.id);
                callback();
            }
        });
    });
}

function runDispatchCycle() {
    var issues = issueQueue.getIssuesReady();
    if (!issues.length) {
        return scheduleNextRun();
    }

    var outstanding = issues.length;
    issues.forEach(function process(issue) {
        sendIssue(issue, function afterSend(err) {
            outstanding -= 1;
            if (err) {
                console.error("Failed to send issue", issue.id, err);
            }
            if (outstanding === 0) {
                scheduleNextRun();
            }
        });
    });
}

function scheduleNextRun() {
    var delay = millisUntilNextSend();
    console.log("Scheduling next email run in", Math.round(delay / 60000), "minutes");
    setTimeout(runDispatchCycle, delay);
}

function startWebhookServer() {
    var port = parseInt(process.env.EMAIL_WEBHOOK_PORT || "4080", 10);
    var server = http.createServer(function handler(req, res) {
        if (req.method !== "POST" || req.url.indexOf("/email/events") !== 0) {
            res.statusCode = 404;
            return res.end();
        }

        var body = "";
        req.on("data", function onData(chunk) {
            body += chunk.toString();
        });

        req.on("end", function onEnd() {
            try {
                var event = JSON.parse(body);
                var normalized = {
                    issue_id: event.issue_id || event.issueId,
                    message_id: event.message_id || event.messageId,
                    recipient: event.recipient || event.email,
                    type: event.type || event.event,
                    payload: event
                };
                store.recordEvent(normalized);
                res.statusCode = 202;
                res.end("ok");
            } catch (err) {
                res.statusCode = 400;
                res.end("invalid payload");
            }
        });
    });

    server.listen(port, function onListen() {
        console.log("Email webhook server listening on", port);
    });
}

function startPollingEvents() {
    var interval = parseInt(process.env.EMAIL_EVENT_POLL_INTERVAL || "300000", 10);
    setInterval(function poll() {
        var pending = issueQueue.loadQueue().filter(function hasPending(issue) {
            return issue.message_ids && issue.message_ids.length;
        });
        pending.forEach(function iterate(issue) {
            (issue.message_ids || []).forEach(function each(id) {
                store.recordEvent({
                    issue_id: issue.id,
                    message_id: id,
                    type: "poll",
                    payload: { note: "polled placeholder" }
                });
            });
        });
    }, interval);
}

function start() {
    console.log("Starting email scheduler for 5:25am ET dispatch window");
    scheduleNextRun();
    startWebhookServer();
    startPollingEvents();
}

if (require.main === module) {
    start();
}

module.exports = {
    start: start,
    scheduleNextRun: scheduleNextRun,
    runDispatchCycle: runDispatchCycle
};
