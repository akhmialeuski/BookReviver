import type { RejectionReason } from '@/api';
import { ProblemCode } from '@/shared/http/codes';
import { pluralize } from '@/shared/lib/format';

/**
 * Every text the interface shows, in one place.
 *
 * Components import from here and never hold a user-facing string of their own, so wording is reviewed and
 * changed in one file and a later translation has a single source. Text that depends on a number is a function.
 */
export const MESSAGES = {
  app: {
    name: 'BookReviver',
    notFound: 'This page does not exist.',
    backHome: 'Back to your books',
  },
  common: {
    close: 'Close',
    cancel: 'Cancel',
    loading: 'Loading…',
    retry: 'Try again',
    previous: 'Previous',
    next: 'Next',
    pageOf: (page: number, pages: number) => `Page ${page} of ${pages}`,
  },
  problems: {
    network: 'The server cannot be reached. Check your connection and try again.',
    unknown: 'Something went wrong. Try again in a moment.',
    unauthorized: 'Your session has ended. Sign in again.',
    forbidden: 'You are not allowed to do this.',
    notFound: 'This item does not exist or was deleted.',
    conflict: 'This cannot be done right now. Try again in a moment.',
    tooLarge: 'The upload is larger than the server accepts.',
    invalidInput: 'Some of the values are not valid.',
    // The wording of a code the account routes send in the detail of a problem
    codes: {
      [ProblemCode.LoginBadCredentials]: 'The email address or the password is wrong.',
      [ProblemCode.LoginUserNotVerified]:
        'This address is not confirmed yet. Follow the link in the message we sent you.',
      [ProblemCode.VerifyBadToken]: 'This confirmation link is not valid or has expired.',
      [ProblemCode.VerifyAlreadyVerified]: 'This address is already confirmed. You can sign in.',
    } as Readonly<Record<string, string>>,
  },
  auth: {
    signIn: {
      title: 'Sign in',
      description: 'Use the email address and password of your account.',
      submit: 'Sign in',
      submitting: 'Signing in…',
      noAccount: 'No account yet?',
      register: 'Create one',
      resend: 'Send a new confirmation link',
      resent: 'If this address has an account, a new confirmation link is on its way.',
    },
    register: {
      title: 'Create an account',
      description: 'Your books stay private to your account.',
      submit: 'Create account',
      submitting: 'Creating…',
      hasAccount: 'Already have an account?',
      signIn: 'Sign in',
      passwordHint: 'At least 12 characters, and not your email address.',
      doneTitle: 'Check your mail',
      doneText:
        'If this address can be registered, we have sent a link to confirm it. Open the link, then sign in.',
    },
    verify: {
      title: 'Confirm your address',
      pending: 'Confirming your address…',
      done: 'Your address is confirmed. You can sign in now.',
      missingToken: 'This link has no confirmation token. Open the link from the message again.',
      signIn: 'Go to sign in',
    },
    fields: {
      email: 'Email address',
      password: 'Password',
    },
    signOut: 'Sign out',
    signingOut: 'Signing out…',
  },
  projects: {
    title: 'Your books',
    empty: 'You have no books yet. Create one to start uploading its scans.',
    create: {
      open: 'New book',
      title: 'New book',
      description: 'Give the book a title. You can upload its scans on the next screen.',
      titleLabel: 'Title',
      subtitleLabel: 'Subtitle (optional)',
      submit: 'Create book',
      submitting: 'Creating…',
    },
    delete: {
      open: 'Delete',
      title: 'Delete this book?',
      description: (title: string) =>
        `“${title}” will be deleted with its uploaded files, scans and pages. This cannot be undone.`,
      submit: 'Delete book',
      submitting: 'Deleting…',
    },
    counts: {
      pages: (count: number) => `${count} ${pluralize(count, 'page', 'pages')}`,
      sources: (count: number) => `${count} ${pluralize(count, 'source', 'sources')}`,
      scans: (count: number) => `${count} ${pluralize(count, 'scan', 'scans')}`,
    },
    updated: (date: string) => `Updated ${date}`,
    untitled: 'Untitled book',
    back: 'All books',
  },
  book: {
    details: 'Description',
    noDetails: 'The description of this book is empty.',
    fields: {
      subtitle: 'Subtitle',
      author: 'Author',
      publisher: 'Publisher',
      place: 'Place of publication',
      year: 'Year',
      edition: 'Edition',
      languages: 'Languages',
      orthography: 'Orthography',
      script: 'Script',
      notes: 'Notes',
    },
    orthography: {
      unknown: 'Unknown',
      'pre-reform': 'Pre-reform',
      modern: 'Modern',
    },
    script: {
      unknown: 'Unknown',
      cyrillic: 'Cyrillic',
      latin: 'Latin',
      mixed: 'Mixed',
    },
    sources: {
      title: 'Sources',
      empty: 'No files have been uploaded to this book yet.',
      scans: (count: number) => `${count} ${pluralize(count, 'scan', 'scans')}`,
      imported: (date: string) => `Imported ${date}`,
    },
    scans: {
      title: 'Scans',
      empty: 'The scans appear here as the uploaded files are imported.',
      all: 'All sources',
      filter: 'Show scans of',
      label: (number: number) => `Scan ${number + 1}`,
      pending: 'Preparing images…',
      size: (width: number, height: number) => `${width} × ${height} px`,
    },
  },
  upload: {
    open: 'Upload files',
    title: 'Upload files',
    description:
      'Choose a folder of page images, or PDF and DjVu files. The files are imported in the order shown below.',
    dropHere: 'Drop a folder or files here',
    or: 'or',
    chooseFolder: 'Choose a folder',
    chooseFiles: 'Choose files',
    reading: 'Reading the dropped files…',
    review: {
      title: (count: number, size: string) =>
        `${count} ${pluralize(count, 'file', 'files')} to upload, ${size}`,
      sortByName: 'Sort by name',
      clear: 'Clear the list',
      remove: (name: string) => `Remove ${name}`,
      moveUp: (name: string) => `Move ${name} up`,
      moveDown: (name: string) => `Move ${name} down`,
      moveFolderUp: (name: string) => `Move the folder ${name} up`,
      moveFolderDown: (name: string) => `Move the folder ${name} down`,
      rootFolder: 'Files chosen one by one',
      empty: 'No files chosen yet.',
      nothingToUpload: 'None of the chosen files can be uploaded.',
    },
    kinds: {
      pdf: 'PDF',
      djvu: 'DjVu',
      tiff: 'TIFF',
      jpeg: 'JPEG',
      'jpeg-2000': 'JPEG 2000',
      png: 'PNG',
    },
    skipped: {
      title: (count: number) => `${count} ${pluralize(count, 'file', 'files')} skipped`,
      description: 'These files are not sent.',
      systemFile: 'System file of the operating system, not a part of the book.',
      unsupported: 'This type of file cannot be imported.',
      duplicate: 'Already in the list.',
    },
    submit: (count: number) => `Upload ${count} ${pluralize(count, 'file', 'files')}`,
    submitting: 'Uploading…',
    sending: (count: number, size: string) =>
      `Sending ${count} ${pluralize(count, 'file', 'files')}, ${size}…`,
  },
  importJob: {
    title: 'Import',
    states: {
      queued: 'Waiting for a worker',
      running: 'Importing',
      succeeded: 'Finished',
      failed: 'Failed',
      cancelled: 'Cancelled',
    },
    progress: (done: number, total: number) =>
      total > 0 ? `${done} of ${total} steps done` : 'Starting…',
    cancel: 'Cancel import',
    cancelling: 'Cancelling…',
    dismiss: 'Dismiss',
    imported: (count: number) =>
      `${count} ${pluralize(count, 'source', 'sources')} imported into the book.`,
    nothingImported: 'No file was imported.',
    rejectedTitle: (count: number) => `${count} ${pluralize(count, 'file', 'files')} not imported`,
    skippedTitle: (count: number) =>
      `${count} ${pluralize(count, 'file', 'files')} not reached before the import was cancelled`,
    failedTitle: 'The import failed',
    reasons: {
      duplicate: 'Already in the book',
      unreadable: 'Cannot be read',
      'unsupported-type': 'Type not accepted',
      'system-file': 'System file',
    } satisfies Record<RejectionReason, string>,
  },
} as const;
