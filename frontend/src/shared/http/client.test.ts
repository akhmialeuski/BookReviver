import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  authCookieLoginApiV1AuthLoginPost,
  listProjectsApiV1ProjectsGet,
  usersCurrentUserApiV1UsersMeGet,
} from '@/api';
import { client } from '@/api/client.gen';
import { configureApiClient } from './client';
import { CSRF_HEADER_NAME } from './csrf';
import { ProblemError } from './problem';

/**
 * The generated client with its interceptors installed, run against a fake `fetch` and a fake cookie jar.
 */

const LOGIN = { username: 'reader@example.com', password: 'secret' };

function problemResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  });
}

function sentRequest(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, call: number): Request {
  const sent = fetchMock.mock.calls[call]?.[0];
  if (!(sent instanceof Request)) {
    throw new Error(`Call ${call} of fetch did not receive a Request.`);
  }
  return sent;
}

describe('the configured API client', () => {
  const fetchMock = vi.fn<typeof fetch>();
  let cookies = '';

  beforeEach(() => {
    fetchMock.mockReset();
    cookies = '';
    client.interceptors.request.clear();
    client.interceptors.error.clear();
    // Node's Request, unlike the browser's, refuses a relative address
    client.setConfig({ fetch: fetchMock, baseUrl: 'http://localhost' });
    configureApiClient(() => cookies);
  });

  it('sends the CSRF cookie as a header on a mutating request', async () => {
    cookies = 'csrftoken=token-1';
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await authCookieLoginApiV1AuthLoginPost({ body: LOGIN, throwOnError: true });

    expect(sentRequest(fetchMock, 0).headers.get(CSRF_HEADER_NAME)).toBe('token-1');
  });

  it('posts the sign-in as a form with the fields username and password', async () => {
    cookies = 'csrftoken=token-1';
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await authCookieLoginApiV1AuthLoginPost({ body: LOGIN, throwOnError: true });

    const sent = sentRequest(fetchMock, 0);
    expect(sent.headers.get('content-type')).toContain('application/x-www-form-urlencoded');
    const form = new URLSearchParams(await sent.text());
    expect(form.get('username')).toBe(LOGIN.username);
    expect(form.get('password')).toBe(LOGIN.password);
  });

  it('sends no CSRF header on a read', async () => {
    cookies = 'csrftoken=token-1';
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, page: 1, size: 50, pages: 0 }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await listProjectsApiV1ProjectsGet({ throwOnError: true });

    expect(sentRequest(fetchMock, 0).headers.has(CSRF_HEADER_NAME)).toBe(false);
  });

  it('fetches a token with a GET first when a mutating request finds no cookie', async () => {
    fetchMock.mockImplementation(async (input) => {
      if (input instanceof Request && input.method === 'GET') {
        cookies = 'csrftoken=fresh';
        return problemResponse(
          { type: 'http-unauthorized', title: 'Unauthorized', status: 401 },
          401,
        );
      }
      return new Response(null, { status: 204 });
    });

    await authCookieLoginApiV1AuthLoginPost({ body: LOGIN, throwOnError: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentRequest(fetchMock, 0).method).toBe('GET');
    expect(sentRequest(fetchMock, 1).headers.get(CSRF_HEADER_NAME)).toBe('fresh');
  });

  it('throws the problem of an error response as a ProblemError with its message', async () => {
    cookies = 'csrftoken=token-1';
    fetchMock.mockResolvedValue(
      problemResponse(
        {
          type: 'http-bad-request',
          title: 'Bad Request',
          status: 400,
          detail: 'LOGIN_BAD_CREDENTIALS',
        },
        400,
      ),
    );

    const failure = authCookieLoginApiV1AuthLoginPost({ body: LOGIN, throwOnError: true });

    await expect(failure).rejects.toBeInstanceOf(ProblemError);
    await expect(failure).rejects.toMatchObject({ status: 400, code: 'LOGIN_BAD_CREDENTIALS' });
  });

  it('throws a network problem when the server cannot be reached', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(usersCurrentUserApiV1UsersMeGet({ throwOnError: true })).rejects.toMatchObject({
      status: null,
    });
  });
});
