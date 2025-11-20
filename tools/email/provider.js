"use strict";

var crypto = require("crypto");
var request = require("request");
var store = require("./store");

var bufferFrom = Buffer.from || function legacyBuffer(str) {
    return new Buffer(str);
};

var EMAIL_PROVIDER = process.env.EMAIL_PROVIDER || "ses";
var DEFAULT_FROM = process.env.EMAIL_FROM || "no-reply@example.com";
var SMTP_HOST = process.env.EMAIL_SMTP_HOST;
var SMTP_PORT = parseInt(process.env.EMAIL_SMTP_PORT || "0", 10) || 587;

function buildSesPayload(options) {
    return {
        Action: "SendRawEmail",
        Source: options.from,
        Destinations: [options.to],
        RawMessage: {
            Data: bufferFrom(options.raw).toString("base64")
        }
    };
}

function buildSmtpPayload(options) {
    return {
        method: "POST",
        url: "smtp://" + SMTP_HOST + ":" + SMTP_PORT,
        auth: {
            user: process.env.EMAIL_SMTP_USER,
            pass: process.env.EMAIL_SMTP_PASS
        },
        form: options
    };
}

function createMessageId() {
    if (crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return crypto.randomBytes(16).toString("hex") + "@brackets";
}

function buildRawMessage(options) {
    var headers = [];
    headers.push("From: " + options.from);
    headers.push("To: " + options.to);
    headers.push("Subject: " + options.subject);
    headers.push("Message-ID: <" + options.messageId + ">\n");
    headers.push("MIME-Version: 1.0");
    headers.push("Content-Type: text/html; charset=UTF-8");
    return headers.join("\r\n") + "\r\n\r\n" + options.html;
}

function sendEmail(options, callback) {
    var messageId = options.messageId || createMessageId();
    var payload = {
        to: options.to,
        from: options.from || DEFAULT_FROM,
        subject: options.subject,
        html: options.html,
        messageId: messageId
    };

    var raw = buildRawMessage(payload);

    var transportOptions;
    if (EMAIL_PROVIDER === "sendgrid" || EMAIL_PROVIDER === "mailgun") {
        transportOptions = {
            method: "POST",
            url: process.env.EMAIL_API_URL,
            headers: {
                Authorization: "Bearer " + (process.env.EMAIL_API_KEY || "")
            },
            json: true,
            body: {
                personalizations: [{ to: [{ email: payload.to }] }],
                from: { email: payload.from },
                subject: payload.subject,
                content: [{ type: "text/html", value: payload.html }],
                headers: { "Message-Id": payload.messageId }
            }
        };
    } else if (EMAIL_PROVIDER === "smtp") {
        transportOptions = buildSmtpPayload({
            from: payload.from,
            to: payload.to,
            subject: payload.subject,
            html: payload.html,
            "message-id": payload.messageId
        });
    } else {
        transportOptions = {
            method: "POST",
            url: process.env.EMAIL_SES_URL || "https://email.us-east-1.amazonaws.com",
            form: buildSesPayload({
                from: payload.from,
                to: payload.to,
                raw: raw
            })
        };
    }

    request(transportOptions, function onComplete(err, res, body) {
        if (err) {
            return callback(err);
        }
        if (res && res.statusCode >= 400) {
            return callback(new Error("Provider rejected message: " + res.statusCode));
        }
        var providerMessageId = messageId;
        if (body && body.MessageId) {
            providerMessageId = body.MessageId;
        }
        store.recordSend(payload.to, options.issueId, providerMessageId, EMAIL_PROVIDER);
        callback(null, providerMessageId);
    });
}

module.exports = {
    sendEmail: sendEmail,
    createMessageId: createMessageId
};
