import type { PageResult } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * What a step found, written as the facts a reader scans: a label and a value in the words of the book.
 *
 * One list serves the section of the open page, which reads the facts of the whole chain of a stage, and each result in
 * the list of results, which reads the facts of the version alone. A fact the step did not report is left out.
 */

const labels = MESSAGES.processing.thisPage;

/** One fact about a result. */
export interface Fact {
  label: string;
  value: string;
}

/**
 * Write the facts of a result.
 *
 * @param result What the step or the steps found.
 * @param unsure Whether the page is marked for a second look, which the confidence then says.
 */
export function factsOf(result: PageResult, unsure: boolean): Fact[] {
  const facts: Fact[] = [];
  if (result.skipped) {
    facts.push({ label: labels.method, value: labels.left });
  } else if (result.angle !== null) {
    facts.push({ label: labels.angle, value: labels.degrees(result.angle) });
  }
  if (result.method !== null) {
    facts.push({ label: labels.binarized, value: labels.methods[result.method] ?? result.method });
  }
  if (result.threshold !== null) {
    facts.push({ label: labels.threshold, value: labels.thresholdValue(result.threshold) });
  }
  if (result.zones !== null) {
    facts.push({ label: labels.pictures, value: String(result.zones.length) });
  }
  if (result.specks !== null) {
    facts.push({ label: labels.specks, value: String(result.specks) });
  }
  if (result.pages !== null) {
    facts.push({ label: labels.pages, value: labels.pagesValue(result.pages) });
  }
  if (result.cutX !== null) {
    facts.push({ label: labels.cut, value: labels.pixels(result.cutX) });
  }
  if (result.slantDeg !== null) {
    facts.push({ label: labels.slant, value: labels.degrees(result.slantDeg) });
  }
  if (result.overlapPx !== null) {
    facts.push({ label: labels.overlap, value: labels.pixels(result.overlapPx) });
  }
  if (result.bend !== null) {
    facts.push({ label: labels.bend, value: labels.bendValue(result.bend) });
  }
  if (result.lines !== null) {
    facts.push({ label: labels.lines, value: String(result.lines) });
  }
  if (result.confidence !== null) {
    facts.push({
      label: labels.confidence,
      value: unsure ? labels.unsure(result.confidence) : labels.sure(result.confidence),
    });
  }
  return facts;
}
