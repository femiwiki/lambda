import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { after, before, describe, it } from "node:test";
import { exportJWK, generateKeyPair, SignJWT } from "jose";

const AUDIENCE = "chart-renderer";
const SUBJECT = "arn:aws:iam::123456789012:role/Femiwiki";

let server;
let issuer;
let privateKey;
let handler;

const sign = (claims = {}, options = {}) => {
	const jwt = new SignJWT(claims)
		.setProtectedHeader({ alg: "ES384", kid: "test" })
		.setIssuer(options.issuer ?? issuer)
		.setAudience(options.audience ?? AUDIENCE)
		.setSubject(options.subject ?? SUBJECT)
		.setIssuedAt();
	if (options.expiresAt !== null) {
		jwt.setExpirationTime(options.expiresAt ?? "1h");
	}
	return jwt.sign(options.key ?? privateKey);
};

const event = (token, body, method = "POST") => ({
	rawPath: `/${token}`,
	body,
	isBase64Encoded: false,
	requestContext: { http: { method } },
});

before(async () => {
	const keys = await generateKeyPair("ES384");
	privateKey = keys.privateKey;
	const jwk = {
		...(await exportJWK(keys.publicKey)),
		kid: "test",
		alg: "ES384",
	};
	server = createServer((_req, res) => {
		res.setHeader("content-type", "application/json");
		res.end(JSON.stringify({ keys: [jwk] }));
	});
	await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
	issuer = `http://127.0.0.1:${server.address().port}`;
	process.env.ISSUER = issuer;
	process.env.AUDIENCE = AUDIENCE;
	process.env.SUBJECT = SUBJECT;
	({ handler } = await import("./dist/index.js"));
});

after(() => server.close());

describe("handler", () => {
	it("renders a chart for a valid token", async () => {
		const body = await readFile(
			"node_modules/chart-service/docs/examples/line-1.json",
			"utf8",
		);
		const result = await handler(event(await sign(), body));
		assert.equal(result.statusCode, 200);
		assert.equal(result.headers["content-type"], "text/html");
		assert.match(result.body, /^<wiki-chart [^>]*><svg /s);
	});

	it("reads a base64 body", async () => {
		const body = await readFile(
			"node_modules/chart-service/docs/examples/pie.json",
		);
		const result = await handler({
			...event(await sign(), body.toString("base64")),
			isBase64Encoded: true,
		});
		assert.equal(result.statusCode, 200);
	});

	for (const [name, makeToken] of Object.entries({
		"no token": async () => "",
		"a malformed token": async () => "not-a-jwt",
		"another audience": () => sign({}, { audience: "https:something-else" }),
		"another subject": () =>
			sign({}, { subject: "arn:aws:iam::123456789012:role/Other" }),
		"another issuer": () => sign({}, { issuer: "https://example.com" }),
		"an expired token": () =>
			sign({}, { expiresAt: Math.floor(Date.now() / 1000) - 120 }),
		"no expiry": () => sign({}, { expiresAt: null }),
		"another key": async () =>
			sign({}, { key: (await generateKeyPair("ES384")).privateKey }),
	})) {
		it(`refuses ${name}`, async () => {
			const result = await handler(event(await makeToken(), "{}"));
			assert.equal(result.statusCode, 401);
		});
	}

	it("refuses an unsigned token", async () => {
		const header = Buffer.from(JSON.stringify({ alg: "none" })).toString(
			"base64url",
		);
		const payload = Buffer.from(
			JSON.stringify({
				iss: issuer,
				aud: AUDIENCE,
				sub: SUBJECT,
				exp: Math.floor(Date.now() / 1000) + 600,
			}),
		).toString("base64url");
		const result = await handler(event(`${header}.${payload}.`, "{}"));
		assert.equal(result.statusCode, 401);
	});

	it("refuses a method other than POST", async () => {
		const result = await handler(event(await sign(), "", "GET"));
		assert.equal(result.statusCode, 405);
	});

	it("refuses a body that is not JSON", async () => {
		const result = await handler(event(await sign(), "{"));
		assert.equal(result.statusCode, 400);
	});

	it("refuses an invalid chart", async () => {
		const body = await readFile(
			"node_modules/chart-service/docs/examples/400.json",
			"utf8",
		);
		const result = await handler(event(await sign(), body));
		assert.equal(result.statusCode, 400);
		assert.ok(JSON.parse(result.body).errors.length > 0);
	});
});
