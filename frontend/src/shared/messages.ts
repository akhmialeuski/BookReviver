import type {
  ContributorRole,
  FileType,
  IdentifierScheme,
  ImagePolicy,
  JobKind,
  JobState,
  LabelStyle,
  Orthography,
  PageKind,
  PageOrigin,
  PageStageStatus,
  RejectionReason,
  ReviewReason,
  RightsStatus,
  Script,
  Stage,
  StageStatus,
} from '@/api';
import type { Problem } from '@/features/about/fields';
import type { Section } from '@/features/about/sections';
import type { SuggestedField } from '@/features/about/suggestion';
import type { OAuthFailure } from '@/features/auth/oauth';
import type { Phase } from '@/features/stages/stages';
import type { PageFilter } from '@/features/workspace/params';
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
      [ProblemCode.ResetBadToken]:
        'This reset link is not valid or has expired. Ask for a new one.',
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
      forgotPassword: 'Forgot password?',
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
      newPassword: 'New password',
    },
    providers: {
      divider: 'or',
      continueWith: (label: string) => `Continue with ${label}`,
      redirecting: (label: string) => `Taking you to ${label}…`,
      callback: {
        title: 'Signing you in',
        pending: 'Finishing the sign-in…',
        back: 'Back to sign in',
      },
      failures: {
        refused: 'The sign-in was cancelled, or the provider did not confirm it. Try again.',
        'invalid-state':
          'This sign-in could not be checked, because it was started in another browser or has expired. Start it again from here.',
        'no-email':
          'The provider did not share your email address, so there is no account to sign in to. Allow access to the address, or register with a password.',
        failed: 'The sign-in with the provider did not work. Try again in a moment.',
      } satisfies Record<OAuthFailure, string>,
    },
    forgotPassword: {
      title: 'Reset your password',
      description:
        'Enter the address of your account and we will send a link to choose a new password.',
      submit: 'Send the link',
      submitting: 'Sending…',
      doneTitle: 'Check your mail',
      doneText: 'If this address has an account, a link to choose a new password is on its way.',
      back: 'Back to sign in',
    },
    resetPassword: {
      title: 'Choose a new password',
      description:
        'The password must have at least 12 characters and not contain your email address.',
      submit: 'Save the password',
      submitting: 'Saving…',
      missingToken: 'This link has no reset token. Open the link from the message again.',
      doneTitle: 'Password changed',
      doneText: 'Your password is changed. Sign in with the new one.',
      signIn: 'Go to sign in',
      askAgain: 'Ask for a new link',
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
    untitled: 'Untitled book',
    back: 'All books',
  },
  book: {
    viewPages: 'View pages',
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
    } satisfies Record<Orthography, string>,
    script: {
      unknown: 'Unknown',
      cyrillic: 'Cyrillic',
      latin: 'Latin',
      mixed: 'Mixed',
    } satisfies Record<Script, string>,
    sources: {
      title: 'Sources',
      empty: 'No files have been uploaded to this book yet.',
      scans: (count: number) => `${count} ${pluralize(count, 'scan', 'scans')}`,
      imported: (date: string) => `Imported ${date}`,
      showScans: 'Show scans',
      viewPages: (name: string) => `View the pages of ${name} in the viewer`,
      viewPagesShort: 'View pages',
      movePages: (name: string) => `Move the pages of ${name}`,
      movePagesShort: 'Move pages',
      remove: (name: string) => `Delete ${name}`,
      removeTitle: 'Delete this file?',
      removeDescription: (name: string, scans: number) =>
        `${name} is deleted with its ${scans} ${pluralize(scans, 'scan', 'scans')}. The pages cut from it stay in the book with their images, but cannot be cut from the scan again.`,
      removeSubmit: 'Delete file',
      removing: 'Deleting…',
    },
    scans: {
      title: 'Scans',
      empty: 'The scans appear here as the uploaded files are imported.',
      showAll: 'Show all scans',
      viewPage: (label: string) => `Open ${label} in the viewer`,
      label: (position: number) => `Scan ${position}`,
      pending: 'Preparing images…',
      size: (width: number, height: number) => `${width} × ${height} px`,
    },
  },
  about: {
    navigation: 'Sections of the description',
    sections: {
      title: 'Title and authors',
      publication: 'Publication',
      copy: 'Physical copy',
      subjects: 'Subjects and rights',
      notes: 'Notes',
      storage: 'Image storage',
    } satisfies Record<Section, string>,
    delete: 'Delete the book',
    fields: {
      title: 'Title',
      subtitle: 'Subtitle',
      parallelTitles: 'Titles in other languages',
      originalTitle: 'Original title',
      contributors: 'Contributors',
      languages: 'Languages',
      orthography: 'Orthography',
      script: 'Script',
      place: 'Place',
      publisher: 'Publisher',
      printer: 'Printer',
      year: 'Year',
      edition: 'Edition',
      censorship: 'Censorship permit',
      series: 'Series',
      seriesNumber: 'Number in the series',
      volume: 'Volume',
      printedPagination: 'Printed pagination',
      height: 'Height, cm',
      illustrations: 'Illustrations',
      binding: 'Binding',
      identifiers: 'Identifiers',
      copyHolder: 'Held by',
      copyNotes: 'Notes on the copy',
      subjects: 'Subjects',
      rights: 'Rights',
      notes: 'Notes',
    },
    hints: {
      parallelTitles: 'One title per line.',
      languages: 'Three-letter ISO 639-3 codes such as rus or bel, separated by spaces.',
      subjects: 'One subject per line.',
      year: 'As printed: 1896, [1896] or 1896-1897.',
      censorship: 'The permit line with its date, as printed on the back of the title page.',
      printedPagination: 'As on a library card, for example XII, 340 p., 8 plates.',
    },
    contributors: {
      name: 'Name as printed',
      role: 'Role',
      add: 'Add a contributor',
      remove: (name: string) => `Remove ${name === '' ? 'the contributor' : name}`,
    },
    identifiers: {
      scheme: 'Kind',
      value: 'Value',
      add: 'Add an identifier',
      remove: (value: string) => `Remove ${value === '' ? 'the identifier' : value}`,
    },
    roles: {
      aut: 'Author',
      edt: 'Editor',
      com: 'Compiler',
      trl: 'Translator',
      ill: 'Illustrator',
      egr: 'Engraver',
      ltg: 'Lithographer',
      pht: 'Photographer',
      wpr: 'Writer of preface',
      win: 'Writer of introduction',
      ann: 'Annotator',
      cmm: 'Commentator',
      dte: 'Dedicatee',
      ctb: 'Contributor',
      oth: 'Other',
    } satisfies Record<ContributorRole, string>,
    schemes: {
      isbn: 'ISBN',
      oclc: 'OCLC number',
      lccn: 'LCCN',
      shelfmark: 'Shelfmark',
      url: 'Web address',
    } satisfies Record<IdentifierScheme, string>,
    rights: {
      unknown: 'Unknown',
      'public-domain': 'Public domain',
      'in-copyright': 'In copyright',
    } satisfies Record<RightsStatus, string>,
    imagePolicy: {
      legend: 'How the images are stored',
      options: {
        compact: {
          label: 'Compact',
          description: 'Gray and colour pages are stored as JPEG, which takes less space.',
        },
        lossless: {
          label: 'Lossless',
          description:
            'Gray and colour pages are stored as PNG, which loses nothing and takes more space.',
        },
      } satisfies Record<ImagePolicy, { label: string; description: string }>,
      note: 'Black-and-white pages are always stored as PNG. A change applies to the images written after it, and the images already stored stay as they are.',
    },
    cover: {
      title: 'Cover in the library',
      empty: 'The book has no pages yet, so there is nothing to choose from.',
      page: (number: number, kind: string) => `#${number} ${kind}`,
      choose: (name: string) => `Use ${name} as the cover`,
      showAll: 'Show all pages',
      showFewer: 'Show fewer pages',
    },
    suggestion: {
      found: (file: string, what: string) => `The file ${file} suggests ${what}.`,
      fields: {
        title: 'a title',
        contributors: 'contributors',
        publisher: 'a publisher',
        publication_year: 'a year',
        languages: 'languages',
        identifiers: 'identifiers',
        subjects: 'subjects',
      } satisfies Record<SuggestedField, string>,
      rows: {
        title: 'Title',
        contributors: 'Contributors',
        publisher: 'Publisher',
        publication_year: 'Year',
        languages: 'Languages',
        identifiers: 'Identifiers',
        subjects: 'Subjects',
      } satisfies Record<SuggestedField, string>,
      use: 'Use in the description',
    },
    status: {
      idle: 'Changes are saved automatically.',
      saving: 'Saving…',
      saved: (when: string) => `Saved automatically · ${when}`,
      failed: (reason: string) => `Not saved: ${reason}`,
      invalid: 'Some changes are not saved yet. Fix the marked fields.',
      retry: 'Try again',
    },
    problems: {
      'title-empty': 'The title cannot be empty.',
      'language-code': 'Use three-letter codes such as rus or bel.',
      'height-range': 'Enter a whole number of centimetres from 1 to 200.',
      'contributor-name': 'Fill in the name of every contributor.',
      'identifier-value': 'Fill in the value of every identifier.',
    } satisfies Record<Problem, string>,
  },
  library: {
    count: (count: number) => `${count} ${pluralize(count, 'book', 'books')}`,
    noFiles: 'No files yet',
    stages: (done: number, total: number) => `${done} of ${total} stages done`,
    next: 'Next stage',
    allDone: 'Everything available is done',
    legend: 'Each bar shows the ten stages of a book.',
  },
  pages: {
    kinds: {
      cover: 'Cover',
      'back-cover': 'Back cover',
      endpaper: 'Endpaper',
      frontispiece: 'Frontispiece',
      title: 'Title page',
      text: 'Text',
      plate: 'Plate',
      blank: 'Blank',
      other: 'Other',
    } satisfies Record<PageKind, string>,
    origins: {
      scan: 'Scan',
      blank: 'Blank leaf',
      placeholder: 'Placeholder',
    } satisfies Record<PageOrigin, string>,
    excluded: 'Excluded',
    unnumbered: 'No number',
    position: (position: number) => `№ ${position}`,
    name: (position: number, label: string) =>
      label === '' ? `page ${position}` : `page ${label} (${position})`,
    noImage: 'No image',
    preparing: 'Preparing image…',
    conflict: (reason: string) =>
      `Nothing was changed: the pages were changed elsewhere or that place is taken. The list shows the pages as they are now.${reason === '' ? '' : ` ${reason}`}`,
    labelStyles: {
      arabic: '1, 2, 3',
      'roman-lower': 'i, ii, iii',
      'roman-upper': 'I, II, III',
      none: 'No numbers (erase the labels)',
    } satisfies Record<LabelStyle, string>,
    strip: {
      title: 'Pages',
      empty: 'The pages appear here once the uploaded files are imported.',
      selectPage: (name: string) => `Select ${name}`,
      openPage: (name: string) => `Open ${name} in the viewer`,
      editPage: (name: string) => `Edit ${name}`,
      selected: (count: number) => `${count} selected`,
      selectAll: 'Select all pages',
      clearSelection: 'Clear the selection',
      moveSelected: 'Move selected',
      number: 'Number pages',
      add: 'Add a page',
      view: 'Open the viewer',
    },
    move: {
      title: (count: number) => `Move ${count} ${pluralize(count, 'page', 'pages')}`,
      sourceTitle: (name: string) => `Move the pages of ${name}`,
      description:
        'The pages stand together before or after the page you choose, in the order they have now.',
      anchor: 'Page to put them next to',
      side: 'Place',
      before: 'Before it',
      after: 'After it',
      noAnchor: 'There is no other page to put them next to.',
      submit: 'Move',
      submitting: 'Moving…',
    },
    edit: {
      title: (name: string) => `Edit ${name}`,
      description:
        'Change the printed number, the kind and the notes of the page, or leave it out of the book.',
      label: 'Printed number',
      labelHint: 'Leave empty for a page without a number.',
      kind: 'Kind of page',
      included: 'Part of the book',
      notes: 'Notes',
      save: 'Save',
      saving: 'Saving…',
      scanSection: 'Scan',
      noScan: 'This page has no scan.',
      attach: 'Bind a scan',
      remove: 'Delete page',
    },
    remove: {
      title: 'Delete this page?',
      description: (name: string) =>
        `The ${name} is deleted with its images and versions. Its scan and its source stay.`,
      submit: 'Delete page',
      submitting: 'Deleting…',
      back: 'Back',
    },
    number: {
      title: 'Number pages',
      description:
        'Writes the printed numbers into the pages from the first to the last one, in book order. Excluded pages and the kinds you skip keep their labels.',
      first: 'First page',
      last: 'Last page',
      style: 'Style',
      start: 'First number',
      bracketed: 'Write the number in square brackets',
      skip: 'Skip these kinds of page',
      submit: 'Write numbers',
      submitting: 'Writing…',
    },
    add: {
      title: 'Add a page',
      description:
        'A placeholder stands for a page you have no scan of yet. A blank leaf is a white page, such as the back of a cover.',
      origin: 'Kind of new page',
      kind: 'Role in the book',
      label: 'Printed number',
      notes: 'Notes',
      place: 'Place',
      atEnd: 'At the end of the book',
      before: 'Before',
      after: 'After',
      anchor: 'Page',
      submit: 'Add page',
      submitting: 'Adding…',
    },
    attach: {
      title: 'Bind a scan',
      description: (name: string) =>
        `Choose the scan that becomes the image of ${name}, which stands for a missing page.`,
      source: 'File',
      scan: (number: number, label: string) =>
        label === '' ? `Scan ${number}` : `Scan ${number} (${label})`,
      noScans: 'This file has no scans yet.',
      noSources: 'There are no files to take a scan from. Upload files to the book first.',
      takeOver: 'Take the scan from the page that shows it now',
      takeOverHint: 'That page is deleted, so the scan is shown by this page only.',
      submit: 'Bind scan',
      submitting: 'Binding…',
      back: 'Back',
    },
  },
  stages: {
    phases: {
      prepare: 'Prepare',
      image: 'Image',
      text: 'Text',
      publish: 'Publish',
    } satisfies Record<Phase, string>,
    names: {
      import: 'Import',
      'page-split': 'Split',
      'page-order': 'Order',
      geometry: 'Geometry',
      cleanup: 'Cleanup',
      layout: 'Layout',
      background: 'Background',
      recognition: 'Recognition',
      proofreading: 'Proofreading',
      typesetting: 'Typesetting',
    } satisfies Record<Stage, string>,
    summaries: {
      import: 'Upload the files of the book. Each file holds scans, and the scans become pages.',
      'page-split': 'Cut every scan of two facing pages into a left page and a right page.',
      'page-order': 'Put the pages in the order of the book, number them and add the missing ones.',
      geometry: 'Straighten each page so its lines of text run level.',
      cleanup: 'Remove specks and stains, and make a black-and-white copy of each page.',
      layout: 'Mark the text, the headings, the pictures and the page numbers on each page.',
      background: 'Give every page of the book one even background.',
      recognition: 'Read the text of each page.',
      proofreading: 'Check the recognised text line by line and correct it.',
      typesetting: 'Set the corrected text as a new edition of the book.',
    } satisfies Record<Stage, string>,
    status: {
      done: 'Done',
      attention: 'Needs a look',
      running: 'Running',
      waiting: 'Waiting',
      unavailable: 'Soon',
    } satisfies Record<StageStatus, string>,
    pageStatus: {
      fresh: 'Up to date',
      stale: 'Out of date',
      failed: 'Failed',
      'not-run': 'Not processed',
    } satisfies Record<PageStageStatus, string>,
    review: {
      'low-confidence': 'Check: the step was not sure of its result',
      'not-applied': 'Check: the step left the page as it was',
    } satisfies Record<ReviewReason, string>,
  },
  viewer: {
    title: 'Pages',
    back: 'Back to the book',
    loading: 'Loading the pages…',
    empty: 'This book has no pages yet. Upload files to the book to see them here.',
    unknownPage: 'The page in this link is not in the book any more, so the first page is shown.',
    noImage: 'This page has no image yet.',
    loadFailed: 'The image of this page could not be loaded. It may still be being prepared.',
    ready: 'Page shown',
    caption: (label: string, position: number, count: number) =>
      `${label === '' ? 'No number' : `Page ${label}`} · ${position} of ${count}`,
    toolbar: {
      first: 'First page',
      previous: 'Previous page',
      next: 'Next page',
      last: 'Last page',
      slider: 'Position in the book',
      goTo: 'Go to position',
      goToSubmit: 'Go',
      goToOf: (count: number) => `of ${count}`,
      fitPage: 'Fit the whole page',
      fitWidth: 'Fit to width',
      zoomIn: 'Zoom in',
      zoomOut: 'Zoom out',
      onePage: 'One page',
      twoPages: 'Two pages',
      showPanel: 'Show the page panel',
      hidePanel: 'Hide the page panel',
    },
    actions: {
      edit: (name: string) => `Edit ${name}`,
      move: (name: string) => `Move ${name}`,
      editShort: 'Edit',
      moveShort: 'Move',
    },
    panel: {
      title: 'Page panel',
      page: (position: number, label: string) =>
        label === '' ? `Page at position ${position}` : `Page ${label}, position ${position}`,
    },
  },
  workspace: {
    unknownStage: 'This book has no such stage.',
    header: {
      library: 'Library',
      read: 'Read the book',
      accountMenu: 'Account menu',
    },
    bar: {
      label: 'Stages of the book',
      about: 'About the book',
      noFiles: 'No files yet',
      waitsForPages: 'Waits for pages',
      files: (files: number, scans: number) =>
        `${files} ${pluralize(files, 'file', 'files')} · ${scans} ${pluralize(scans, 'scan', 'scans')}`,
      pages: (count: number) => `${count} ${pluralize(count, 'page', 'pages')}`,
      done: (done: number, total: number) => `${done} of ${total}`,
      check: (count: number) => `${count} ${pluralize(count, 'page', 'pages')} to check`,
      failed: (count: number) => `${count} failed`,
    },
    layout: {
      toggleStrip: 'Show or hide the pages',
      togglePanel: 'Show or hide the stage panel',
    },
    strip: {
      title: 'Pages',
      countOf: (shown: number, total: number) => `${shown} of ${total}`,
      filters: {
        all: 'All',
        check: (count: number) => `Check ${count}`,
        leftOut: (count: number) => `Left out ${count}`,
      },
      grid: 'Show the pages as a grid',
      list: 'Show the pages as a strip',
      leftOut: 'Left out',
      empty: {
        all: 'This book has no pages yet.',
        check: 'No page needs a look in this stage.',
        'left-out': 'No page is left out of the book.',
      } satisfies Record<PageFilter, string>,
    },
    grid: {
      selected: (count: number) =>
        count === 0
          ? 'No pages selected'
          : `${count} ${pluralize(count, 'page', 'pages')} selected`,
      clear: 'Clear the selection',
    },
    canvas: {
      toolbar: 'Page controls',
      compare: 'Before / after',
      compareSoon: 'Comparing a page with the stage before it comes with processing',
      empty: 'This book has no pages yet. Add files on the Import stage to see them here.',
      chip: (label: string, kind: string) => (label === '' ? kind : `p. ${label} · ${kind}`),
    },
  },
  activity: {
    title: 'Activity',
    thisBook: 'This book',
    idle: 'Activity',
    empty: 'Nothing has run in this book yet.',
    stop: 'Stop',
    kinds: {
      'import-source': 'Import',
      'prepare-pages': 'Preparing pages',
      'run-stage': 'Stage run',
      'preview-step': 'Preview',
      'cut-tiles': 'Cutting tiles',
      'collect-versions': 'Clearing old results',
    } satisfies Record<JobKind, string>,
    chip: (kind: string, progress: { done: number; total: number }) =>
      progress.total > 0 ? `${kind} · ${progress.done} of ${progress.total}` : `${kind}…`,
    detail: (state: JobState, time: string, progress: { done: number; total: number }) => {
      const at = time === '' ? '' : ` ${time}`;
      switch (state) {
        case 'queued':
          return 'Waiting to start';
        case 'running':
          return progress.total > 0
            ? `Started${at} · ${progress.done} of ${progress.total} done`
            : `Started${at}`;
        case 'succeeded':
          return `Finished${at}`;
        case 'failed':
          return `Failed${at}`;
        case 'cancelled':
          return `Stopped${at}`;
      }
    },
  },
  shortcuts: {
    open: 'Keyboard shortcuts',
    title: 'Keyboard shortcuts',
    hint: 'Press ? anywhere in a book to see this.',
    groups: [
      {
        title: 'Pages',
        items: [
          { label: 'Previous or next page', keys: ['←', '→'] },
          { label: 'First or last page', keys: ['Home', 'End'] },
        ],
      },
      {
        title: 'Stages',
        items: [
          { label: 'Go to stage 1 to 9', keys: ['Alt', '1…9'] },
          { label: 'Go to Typesetting', keys: ['Alt', '0'] },
          { label: 'About the book', keys: ['Alt', 'I'] },
        ],
      },
      {
        title: 'Screen',
        items: [{ label: 'Show these shortcuts', keys: ['?'] }],
      },
    ],
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
    } satisfies Record<FileType, string>,
    skipped: {
      title: (count: number) => `${count} ${pluralize(count, 'file', 'files')} skipped`,
      description: 'These files are not sent.',
      reasons: {
        'system-file': 'System file of the operating system, not a part of the book.',
        'unsupported-type': 'This type of file cannot be imported.',
        duplicate: 'Already in the list.',
      },
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
    } satisfies Record<JobState, string>,
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
