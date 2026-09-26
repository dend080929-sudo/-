function splitHeaderBody(raw) {
  const index = raw.search(/\r?\n\r?\n/);
  if (index < 0) return { headers: raw, body: "" };
  return { headers: raw.slice(0, index), body: raw.slice(index).replace(/^\r?\n\r?\n/, "") };
}

function parseHeaders(raw) {
  const result = {};
  const unfolded = raw.replace(/\r?\n[ \t]+/g, " ");
  for (const line of unfolded.split(/\r?\n/)) {
    const index = line.indexOf(":");
    if (index > 0) result[line.slice(0, index).trim().toLowerCase()] = line.slice(index + 1).trim();
  }
  return result;
}

function parameter(value, name) {
  const match = String(value || "").match(new RegExp("(?:^|;)\\s*" + name + "\\s*=\\s*(?:\\\"([^\\\"]*)\\\"|([^;\\s]*))", "i"));
  return match ? (match[1] || match[2] || "") : "";
}

function bytesFromBinary(value) {
  const bytes = new Uint8Array(value.length);
  for (let i = 0; i < value.length; i++) bytes[i] = value.charCodeAt(i) & 255;
  return bytes;
}

function decodeBase64Bytes(value) {
  try { return bytesFromBinary(atob(value.replace(/[\r\n\t ]/g, ""))); } catch (_) { return new TextEncoder().encode(value); }
}

function decodeQuotedPrintableBytes(value) {
  const output = [];
  const normalized = value.replace(/=\r?\n/g, "");
  for (let i = 0; i < normalized.length; i++) {
    if (normalized[i] === "=" && /^[0-9A-F]{2}$/i.test(normalized.slice(i + 1, i + 3))) {
      output.push(parseInt(normalized.slice(i + 1, i + 3), 16)); i += 2;
    } else {
      output.push(normalized.charCodeAt(i) & 255);
    }
  }
  return new Uint8Array(output);
}

function decodeBytes(bytes, charset) {
  const labels = [charset || "utf-8", "utf-8", "windows-1252"];
  for (const label of labels) {
    try { return new TextDecoder(label, { fatal: false }).decode(bytes); } catch (_) {}
  }
  return Array.from(bytes, byte => String.fromCharCode(byte)).join("");
}

function decodeTransfer(body, headers) {
  const encoding = String(headers["content-transfer-encoding"] || "").toLowerCase();
  if (encoding === "base64") return decodeBase64Bytes(body);
  if (encoding === "quoted-printable") return decodeQuotedPrintableBytes(body);
  return new TextEncoder().encode(body);
}

function decodeMimeWord(value) {
  return String(value || "").replace(/=\?([^?]+)\?([bBqQ])\?([^?]+)\?=/g, (_, charset, mode, data) => {
    const bytes = mode.toLowerCase() === "b"
      ? decodeBase64Bytes(data)
      : decodeQuotedPrintableBytes(data.replace(/_/g, " "));
    return decodeBytes(bytes, charset);
  });
}

function htmlToText(value) {
  return decodeMimeWord(value)
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<head[\s\S]*?<\/head>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<svg[\s\S]*?<\/svg>/gi, "")
    .replace(/<img[^>]*alt=["']([^"']*)["'][^>]*>/gi, " [$1] ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p\s*>/gi, "\n\n")
    .replace(/<\/div\s*>/gi, "\n")
    .replace(/<\/li\s*>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ").replace(/&amp;/gi, "&").replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">").replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/[ \t]+/g, " ").replace(/\n[ \t]+/g, "\n").replace(/\n{3,}/g, "\n\n")
    .trim();
}

function safeFilename(value) {
  return decodeMimeWord(value).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 160);
}

function parsePart(raw, result) {
  const part = splitHeaderBody(raw);
  const headers = parseHeaders(part.headers);
  const type = String(headers["content-type"] || "text/plain").toLowerCase();
  const disposition = String(headers["content-disposition"] || "").toLowerCase();
  const boundary = parameter(type, "boundary");

  if (boundary) {
    for (const child of part.body.split("--" + boundary)) {
      if (child === "" || child === "--" || child.trim() === "") continue;
      parsePart(child.replace(/--\s*$/, ""), result);
    }
    return;
  }

  const filename = parameter(headers["content-disposition"], "filename") || parameter(type, "name");
  const bytes = decodeTransfer(part.body, headers);
  const charset = parameter(type, "charset") || "utf-8";
  const text = decodeBytes(bytes, charset);
  const isAttachment = disposition.includes("attachment") || !!filename || !type.startsWith("text/");

  if (isAttachment) {
    result.attachments.push({
      filename: safeFilename(filename || "添付ファイル"),
      content_type: type.split(";")[0].trim(),
      size: bytes.byteLength,
      inline: disposition.includes("inline")
    });
  } else if (type.includes("text/plain")) {
    result.plain.push(text);
  } else if (type.includes("text/html")) {
    result.html.push(text);
  }
}

function extractMail(raw) {
  const result = { plain: [], html: [], attachments: [] };
  parsePart(raw, result);
  let text = result.plain.map(value => value.trim()).filter(Boolean).join("\n\n");
  if (!text) text = result.html.map(htmlToText).filter(Boolean).join("\n\n");
  if (!text) text = result.attachments.length ? "（本文なし・添付ファイルあり）" : "（本文なし）";
  return { text: text.slice(0, 12000), attachments: result.attachments.slice(0, 20) };
}

export default {
  async email(message, env) {
    const raw = await new Response(message.raw).text();
    const extracted = extractMail(raw);
    const data = {
      from: message.from,
      to: message.to,
      subject: decodeMimeWord(message.headers.get("subject") || ""),
      text: extracted.text,
      attachments: extracted.attachments,
      date: message.headers.get("date") || "",
      message_id: message.headers.get("message-id") || ""
    };
    const response = await fetch(env.PYTHON_MAIL_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Mail-Webhook-Token": env.MAIL_WEBHOOK_TOKEN },
      body: JSON.stringify(data)
    });
    if (!response.ok) throw new Error("Python mail webhook failed: " + response.status);
  }
};
