/*
 * Utilities to decorate outbound email content with UTM parameters
 * and a transparent tracking pixel.
 */
"use strict";

const { URL } = require("url");

function appendUtmParameters(html, params) {
    if (!params || Object.keys(params).length === 0) {
        return html;
    }

    return html.replace(/href="(.*?)"/g, function replaceHref(match, href) {
        try {
            const url = new URL(href);
            Object.keys(params).forEach(function apply(key) {
                if (params[key]) {
                    url.searchParams.set(key, params[key]);
                }
            });
            return "href=\"" + url.toString() + "\"";
        } catch (err) {
            return match;
        }
    });
}

function injectOpenTrackingPixel(html, pixelBaseUrl, messageId, issueId) {
    if (!pixelBaseUrl) {
        return html;
    }

    var pixelUrl = pixelBaseUrl;
    var separator = pixelBaseUrl.indexOf("?") === -1 ? "?" : "&";
    pixelUrl += separator + "message_id=" + encodeURIComponent(messageId);
    pixelUrl += "&issue_id=" + encodeURIComponent(issueId);

    var pixelTag = "<img src=\"" + pixelUrl + "\" alt=\"\" width=\"1\" height=\"1\" style=\"display:none;\" />";

    if (html.indexOf("</body>") !== -1) {
        return html.replace("</body>", pixelTag + "</body>");
    }

    return html + "\n" + pixelTag;
}

module.exports = {
    appendUtmParameters: appendUtmParameters,
    injectOpenTrackingPixel: injectOpenTrackingPixel
};
