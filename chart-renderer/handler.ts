/**
 * Renders a chart for MediaWiki's Chart extension, which posts its request to
 * $wgChartServiceUrl and puts the HTML of the response, an SVG in a
 * <wiki-chart> element, into the page.
 *
 * Chart cannot add headers to that request, so the caller proves who it is in
 * the URL path instead: the path is a JWT that AWS STS signed for the wiki's
 * instance role with GetWebIdentityToken. Nothing secret is stored anywhere, and
 * a token copied out of a log stops working when it expires, within an hour.
 *
 * The function URL is open to anyone, so a request is checked before its body is
 * read: the token's signature against the account's JWKS, its issuer, audience,
 * subject and expiry.
 */

import type {
	LambdaFunctionURLEvent,
	LambdaFunctionURLResult,
} from "aws-lambda";
import {
	type RenderParams,
	renderChart,
} from "chart-service/src/lib/render.js";
import { validateRequestParams } from "chart-service/src/lib/validation.js";
import { createRemoteJWKSet, jwtVerify } from "jose";

let jwks: ReturnType<typeof createRemoteJWKSet> | undefined;

export const authorized = async (token: string): Promise<boolean> => {
	// The issuer of GetWebIdentityToken's JWTs for this account, e.g.
	// https://<uuid>.tokens.sts.global.api.aws
	const issuer = process.env.ISSUER;
	// The audience the wiki asks STS for
	const audience = process.env.AUDIENCE;
	// The role of the wiki's instances, which STS puts in sub
	const subject = process.env.SUBJECT;
	if (!issuer || !audience || !subject || !token) {
		return false;
	}
	// Kept between invocations of a warm instance; jose refetches the set when a
	// token names a key it has not seen.
	jwks ??= createRemoteJWKSet(new URL(`${issuer}/.well-known/jwks.json`));
	try {
		await jwtVerify(token, jwks, {
			issuer,
			audience,
			subject,
			algorithms: ["ES384"],
			requiredClaims: ["exp"],
		});
		return true;
	} catch {
		return false;
	}
};

const json = (statusCode: number, body: unknown): LambdaFunctionURLResult => ({
	statusCode,
	headers: { "content-type": "application/json" },
	body: JSON.stringify(body),
});

export const handler = async (
	event: LambdaFunctionURLEvent,
): Promise<LambdaFunctionURLResult> => {
	if (!(await authorized(event.rawPath.replace(/^\/+/, "")))) {
		return { statusCode: 401 };
	}
	if (event.requestContext.http.method !== "POST") {
		return { statusCode: 405, headers: { allow: "POST" } };
	}

	let params: unknown;
	try {
		const body = event.isBase64Encoded
			? Buffer.from(event.body ?? "", "base64").toString()
			: (event.body ?? "");
		params = JSON.parse(body);
	} catch {
		return json(400, { message: "Invalid JSON" });
	}

	const errors = validateRequestParams(params);
	if (errors.length > 0) {
		return json(400, { message: "Invalid request data", errors });
	}

	return {
		statusCode: 200,
		headers: { "content-type": "text/html" },
		body: renderChart(params as RenderParams),
	};
};
