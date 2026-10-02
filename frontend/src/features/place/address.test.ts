import { describe, expect, it } from 'vitest';
import type { BookPlaceSchema } from '@/api';
import {
  addressOfPlace,
  bodyOf,
  type PlaceAddress,
  placeOfBody,
  sameAddress,
} from '@/features/place/address';

const WORKSPACE: PlaceAddress = { mode: 'workspace', stage: 'geometry', page: 'p-1' };
const WRITTEN_AT = new Date('2026-10-02T10:00:00Z');

describe('sameAddress', () => {
  it('counts a field left out as its default', () => {
    expect(
      sameAddress(WORKSPACE, { ...WORKSPACE, view: 'page', compare: 'off', filter: 'all' }),
    ).toBe(true);
  });

  it('tells apart any field that differs', () => {
    const others: PlaceAddress[] = [
      { ...WORKSPACE, mode: 'reading' },
      { ...WORKSPACE, stage: 'cleanup' },
      { ...WORKSPACE, page: 'p-2' },
      { ...WORKSPACE, scan: 's-1' },
      { ...WORKSPACE, source: 'f-1' },
      { ...WORKSPACE, view: 'grid' },
      { ...WORKSPACE, compare: 'side' },
      { ...WORKSPACE, filter: 'check' },
    ];
    for (const other of others) {
      expect(sameAddress(WORKSPACE, other)).toBe(false);
    }
  });
});

describe('bodyOf', () => {
  it('names every field, with null for what the address leaves out', () => {
    expect(bodyOf(WORKSPACE, null, null)).toEqual({
      mode: 'workspace',
      stage: 'geometry',
      page_id: 'p-1',
      scan_id: null,
      source_id: null,
      view: 'page',
      compare: 'off',
      filter: 'all',
      canvas: null,
      strip_page_id: null,
    });
  });

  it('carries the canvas and the first page of the strip', () => {
    const canvas = { zoom: 2, centre_x: 0.3, centre_y: 0.4 };
    expect(bodyOf(WORKSPACE, canvas, 'p-9')).toMatchObject({ canvas, strip_page_id: 'p-9' });
  });
});

describe('placeOfBody and addressOfPlace', () => {
  it('give back the address that wrote the place', () => {
    const address: PlaceAddress = {
      mode: 'workspace',
      stage: 'import',
      scan: 's-1',
      source: 'f-1',
      view: 'grid',
      compare: 'swipe',
      filter: 'wide',
    };
    const place: BookPlaceSchema = placeOfBody(bodyOf(address, null, null), WRITTEN_AT);
    expect(place.updated_at).toBe(WRITTEN_AT.toISOString());
    expect(sameAddress(addressOfPlace(place), address)).toBe(true);
  });
});
