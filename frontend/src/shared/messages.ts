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
  PageStageStatus,
  RejectionReason,
  ReviewReason,
  RightsStatus,
  RuleCondition,
  Script,
  Stage,
  StageStatus,
} from '@/api';
import type { Problem } from '@/features/about/fields';
import type { Section } from '@/features/about/sections';
import type { SuggestedField } from '@/features/about/suggestion';
import type { OAuthFailure } from '@/features/auth/oauth';
import type { RoadmapKey } from '@/features/stages/roadmap';
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
    },
    untitled: 'Untitled book',
  },
  book: {
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
      scans: (count: number) => `${count} ${pluralize(count, 'scan', 'scans')}`,
      imported: (date: string) => `Imported ${date}`,
      remove: 'Delete this file…',
      removeTitle: 'Delete this file?',
      removeDescription: (name: string, scans: number) =>
        `${name} is deleted with its ${scans} ${pluralize(scans, 'scan', 'scans')}. The pages cut from it stay in the book with their images, but cannot be cut from the scan again.`,
      removeSubmit: 'Delete file',
      removing: 'Deleting…',
    },
    scans: {
      empty: 'The scans appear here as the uploaded files are imported.',
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
    excluded: 'Excluded',
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
    edit: {
      title: (name: string) => `Edit ${name}`,
      description:
        'Change the printed number, the kind and the notes of the page, or leave it out of the book.',
      label: 'Printed number',
      labelHint: 'Leave empty for a page without a number.',
      kind: 'Kind of page',
      included: 'Part of the book',
      notes: 'Notes',
      group: 'Group',
      groupHint:
        'A name of your own for pages that a stage should treat alike. Leave empty for no group.',
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
      'unsure-gutter': 'Check: the gutter of the spread was not found for certain',
      'narrow-gutter': 'Check: narrow scan with a gutter in the middle',
      'cut-by-edge': 'Check: text may be cut by the edge of the scan',
      'size-differs': 'Check: the text of this page differs too much in size',
      'few-lines': 'Check: too few lines of text to tell how the page is bent',
      'high-residual': 'Check: the lines are still bent after dewarping',
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
      otherStages: 'Other stages',
      noFiles: 'No files yet',
      waitsForPages: 'Waits for pages',
      files: (files: number, scans: number) =>
        `${files} ${pluralize(files, 'file', 'files')} · ${scans} ${pluralize(scans, 'scan', 'scans')}`,
      pages: (count: number) => `${count} ${pluralize(count, 'page', 'pages')}`,
      done: (done: number, total: number) => `${done} of ${total}`,
      check: (count: number) => `${count} ${pluralize(count, 'page', 'pages')} to check`,
      failed: (count: number) => `${count} failed`,
      stopped: (count: number) =>
        `${count} ${pluralize(count, 'page', 'pages')} stopped before the last step`,
    },
    layout: {
      toggleStrip: 'Show or hide the pages',
      togglePanel: 'Show or hide the stage panel',
      panelTitle: 'Stage panel',
    },
    strip: {
      title: 'Pages',
      countOf: (shown: number, total: number) => `${shown} of ${total}`,
      filters: {
        all: 'All',
        check: (count: number) => `Check ${count}`,
        leftOut: (count: number) => `Left out ${count}`,
        wide: (count: number) => `Wide ${count}`,
      },
      stopped: {
        label: 'Stopped at step',
        all: 'Any step',
        option: (number: number, pages: number) => `Stopped at step ${number} · ${pages}`,
      },
      variant: {
        label: 'Variant',
        all: 'All variants',
        option: (name: string, pages: number) => `${name} · ${pages}`,
        none: 'Not processed',
        mark: (name: string, pinned: boolean) => (pinned ? `${name} · pinned` : name),
      },
      grid: 'Show the pages as a grid',
      list: 'Show the pages as a strip',
      leftOut: 'Left out',
      empty: {
        all: 'This book has no pages yet.',
        check: 'No page needs a look in this stage.',
        'left-out': 'No page is left out of the book.',
        wide: 'No scan of this book is wider than tall.',
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
  order: {
    toolbar: {
      label: 'Layout of the pages',
      pages: 'Pages',
      spreads: 'Spreads',
      number: 'Number pages',
      insert: 'Insert',
      size: 'Size',
    },
    grid: {
      empty: 'This book has no pages yet. Add files on the Import stage to see them here.',
      hintDrag: 'Drag pages to reorder',
      hintRange: 'click to select a range',
      hintAdd: 'click to add one',
      shift: 'Shift',
      ctrl: 'Ctrl',
      dismiss: 'Dismiss',
    },
    tile: {
      noNumber: 'no number',
      position: (position: number) => `#${position}`,
      printed: (label: string) => `p. ${label}`,
      leftOut: 'Left out of the book',
      missing: 'Missing page',
      renumbered: (before: string, after: string) => `The number ${before} becomes ${after}`,
      hidden: 'The number is erased',
    },
    gap: {
      title: (first: string, last: string, count: number) =>
        count === 1 ? `p. ${first} missing?` : `p. ${first}–${last} missing?`,
      jump: (from: string, to: string) => `The numbers jump from ${from} to ${to}`,
      add: 'Add missing',
      adding: 'Adding…',
    },
    drag: {
      stack: (count: number) => `${count} pages`,
      instructions:
        'To pick up a page, press Space. Move it with the arrow keys, drop it with Space, and cancel with Escape. A selected page takes the whole selection with it.',
      pickedUp: (name: string, count: number) =>
        count > 1 ? `Picked up ${name} with ${count - 1} more.` : `Picked up ${name}.`,
      over: (name: string) => `Over ${name}.`,
      overSelf: 'Over the pages being moved, which is no place.',
      dropped: (name: string) => `Dropped next to ${name}.`,
      cancelled: 'Move cancelled. The pages stay where they were.',
    },
    panel: {
      nothing:
        'Select pages to change their kind, number or place. A click selects a page, Shift with a click a range, and Ctrl with a click adds one.',
      selected: (count: number, range: string) =>
        `${count} ${pluralize(count, 'page', 'pages')} selected${range === '' ? '' : ` · ${range}`}`,
      range: (first: string, last: string) => (first === last ? first : `${first}–${last}`),
      more: (count: number) => `+${count}`,
      kind: 'What these pages are',
      mixedKind: 'Different kinds',
      included: 'Part of the book',
      includedHint: 'Off: kept, but left out of every later stage',
      includedMixed: 'Some of these pages are left out of the book.',
      label: 'Printed number',
      labelHint: 'Leave empty for a page without a number.',
      notes: 'Notes',
      notesMixed: 'The notes differ between these pages. Typing here replaces them all.',
      actions: 'Actions',
      move: 'Move to another place…',
      number: 'Number from here…',
      insert: 'Insert a page before / after…',
      attach: 'Attach a scan…',
      delete: (count: number) => `Delete ${count} ${pluralize(count, 'page', 'pages')}…`,
      places: {
        title: (count: number) => `${count} ${pluralize(count, 'place', 'places')} to check.`,
        jump: (from: string, to: string) => `The numbers jump from ${from} to ${to}.`,
        jumps: (count: number) => `The numbers jump in ${count} places.`,
        waiting: (name: string) => `${name} has no scan yet.`,
        waitingMany: (count: number) => `${count} pages have no scan yet.`,
        show: 'Show them',
        showNext: 'Show the next one',
      },
    },
    insert: {
      blankBefore: 'Blank leaf before',
      blankAfter: 'Blank leaf after',
      missingBefore: 'Missing page before',
      missingAfter: 'Missing page after',
      blankEnd: 'Blank leaf at the end',
      missingEnd: 'Missing page at the end',
    },
    numbering: {
      title: 'Number pages',
      description:
        'Writes the printed numbers into a run of pages, in book order. You see the result on the pages before anything is saved.',
      preview:
        'Preview. New numbers are shown in blue, the old ones struck out. Nothing is saved until you apply.',
      first: 'From',
      last: 'To',
      start: 'First number',
      style: 'Style',
      styles: {
        arabic: '1, 2, 3',
        'roman-lower': 'i, ii, iii',
        'roman-upper': 'I, II, III',
        none: 'None',
      } satisfies Record<LabelStyle, string>,
      bracketed: 'In brackets',
      bracketedHint: 'For numbers not printed on the page, like [1]',
      skip: 'Pages that get no number',
      skipHint: 'They keep their place but are skipped by the count, as in a printed book.',
      page: (position: number, label: string, kind: string) =>
        [`#${position}`, label, kind].filter((part) => part !== '').join(' · '),
      backwards: 'The first page stands after the last page. Choose a range that runs forward.',
      counts: (numbered: number, skipped: number) =>
        `${numbered} ${pluralize(numbered, 'page gets', 'pages get')} a new number · ${skipped} ${pluralize(skipped, 'is', 'are')} skipped`,
      loading: 'Working out the numbers…',
      hides: {
        title: 'This hides a gap.',
        text: (from: string, to: string) =>
          `The printed numbers jump from ${from} to ${to}. Numbering straight through makes the jump disappear, and the missing pages with it.`,
        textMany: (count: number) =>
          `The printed numbers jump in ${count} places. Numbering straight through makes the jumps disappear, and the missing pages with them.`,
        addFirst: 'Add the missing pages first',
        twoRuns: 'Number in two runs',
      },
      apply: 'Apply numbers',
      applying: 'Applying…',
    },
    move: {
      title: (count: number) => `Move ${count} ${pluralize(count, 'page', 'pages')}`,
      sourceTitle: (name: string) => `Move the pages of ${name}`,
      description: (count: number) =>
        count === 1
          ? 'Pick the page to put it next to.'
          : `The ${count} pages stay together and keep their order. Pick the page to put them next to.`,
      before: 'Before',
      after: 'After',
      side: 'Side of the page',
      search: 'Page number or position',
      searchLabel: 'Find a page',
      noMatch: 'No page has that number or position.',
      strip: 'Pages to put them next to',
      stripPage: (name: string) => `Put them next to ${name}`,
      moving: 'Moves with the others',
      reading: 'The book will read',
      more: '…',
      noAnchor: 'There is no other page to put them next to.',
      submit: (side: 'before' | 'after', name: string) => `Move ${side} ${name}`,
      submitting: 'Moving…',
    },
    remove: {
      title: (count: number) => `Delete ${count} ${pluralize(count, 'page', 'pages')}?`,
      description:
        'The pages are deleted with their images and versions. Their scans and files stay.',
      submit: (count: number) => `Delete ${count} ${pluralize(count, 'page', 'pages')}`,
      submitting: 'Deleting…',
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
      'measure-book': 'Measuring the book',
    } satisfies Record<JobKind, string>,
    stageRun: (stage: string) => `${stage} run`,
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
        title: 'Page editors',
        items: [
          { label: 'Move the split line by 1 px or by 10 px', keys: ['←', '→', 'Shift'] },
          { label: 'Turn the page by 0.1°', keys: ['Alt', 'Wheel'] },
          { label: 'Take back the last change', keys: ['Ctrl', 'Z'] },
        ],
      },
      {
        title: 'Screen',
        items: [{ label: 'Show these shortcuts', keys: ['?'] }],
      },
    ],
  },
  upload: {
    open: 'Add files',
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
    dismiss: 'Dismiss',
    imported: (count: number) =>
      `${count} ${pluralize(count, 'file', 'files')} imported into the book.`,
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
  import: {
    empty: {
      hint: 'PDF, DjVu, TIFF, JPEG or PNG files, or a whole folder of images',
      path: 'The road of the files to the pages',
      steps: {
        files: { name: 'Files', text: 'What you uploaded: a PDF, a DjVu or a folder of images' },
        scans: { name: 'Scans', text: 'Every image inside the files, as the scanner made it' },
        pages: { name: 'Pages', text: 'The pages of the book, which every later stage works on' },
      },
      tipsTitle: 'Good to know',
      tips: [
        'Scans at 300–600 dpi give the best recognition later on.',
        'A scan of an open book with two pages is fine: the next stage, Split, cuts it in two.',
        'Files of the same book in several parts can be added together. Their pages are put in the order of the files, and Order fixes the rest.',
        'The original files are kept untouched. Every stage can be run again later.',
      ],
    },
    files: {
      title: 'Files of this book',
      summary: (files: number, scans: number, size: string) =>
        `${files} ${pluralize(files, 'file', 'files')} · ${scans} ${pluralize(scans, 'scan', 'scans')} · ${size}`,
      kinds: {
        pdf: 'PDF document',
        djvu: 'DjVu document',
        tiff: 'TIFF image',
        jpeg: 'JPEG image',
        'jpeg-2000': 'JPEG 2000 image',
        png: 'PNG image',
      } satisfies Record<FileType, string>,
      done: 'Imported',
      importing: 'Importing files',
      waiting: 'Waiting to start',
      progress: (done: number, total: number) =>
        total > 0 ? `Importing ${done} of ${total}` : 'Starting…',
      stop: 'Stop',
      stopping: 'Stopping…',
    },
    scans: {
      title: (file: string) => `Scans of ${file}`,
      hint: 'Click a scan to see it large',
    },
    viewer: {
      back: 'Back to the files',
      toolbar: 'Scan controls',
      caption: (position: number, total: number) => `Scan ${position} of ${total}`,
      chip: (position: number, file: string) => `Scan ${position} · ${file}`,
    },
    panel: {
      becamePages: (scans: number, pages: number, places: string) =>
        `A file you uploaded. Its ${scans} ${pluralize(scans, 'scan', 'scans')} became ${pluralize(pages, 'page', 'pages')} ${places} of the book.`,
      noPages: 'A file you uploaded. No page of the book comes from it.',
      file: 'File',
      type: 'Type',
      size: 'Size',
      scans: 'Scans',
      resolution: 'Resolution',
      resolutionValue: (lowest: number, highest: number) =>
        lowest === highest ? `${lowest} dpi` : `${lowest}–${highest} dpi`,
      imported: 'Imported',
      found: 'Found inside the file',
      pages: 'Its pages',
      showInOrder: 'Show its pages in Order',
      moveElsewhere: 'Put its pages somewhere else…',
      deleteNote:
        'Its pages stay in the book with their images, but can no longer be cut from the file again.',
    },
    bar: {
      progress: (done: number, total: number) =>
        total > 0 ? `Importing ${done} of ${total}` : 'Importing…',
    },
  },
  profiles: {
    menu: 'Recipe profiles',
    save: {
      open: 'Save as profile',
      hint: 'Keep these steps in your account, to apply them to other books',
      title: 'Save as a profile',
      description:
        'The steps on the screen are kept in your account, in their order and with their settings and switches. Any of your books can apply them.',
      nameLabel: 'Name of the profile',
      submit: 'Save the profile',
      submitting: 'Saving…',
      saved: (name: string) =>
        `Saved the profile “${name}”. Rename it or make it the default for new books in the profiles of your account.`,
    },
    apply: {
      open: 'Apply profile',
      hint: 'Add the steps of a saved profile to this book as a variant',
      title: 'Apply a profile',
      description:
        'The profile becomes a variant of this stage. No page is processed again until the stage is run.',
      choose: 'Profile',
      option: (name: string, isDefault: boolean, steps: number) =>
        `${name}${isDefault ? ' · default' : ''} · ${steps} ${pluralize(steps, 'step', 'steps')}`,
      none: 'You have no profile for this stage yet. Save a recipe as a profile first.',
      activate: 'Make it the active recipe',
      activateHint:
        'The stage runs by it from now on, and the pages the old recipe made go out of date',
      submit: 'Apply the profile',
      submitting: 'Applying…',
      loadFailed: 'The profiles could not be read.',
      applied: (name: string, active: boolean) =>
        active
          ? `Applied the profile “${name}”. It is the active recipe of this book now.`
          : `Applied the profile “${name}” as a variant of this book.`,
      leftOut: (processors: readonly string[]) =>
        `Left out, since no such processor is installed on this machine: ${processors.join(', ')}.`,
    },
    page: {
      title: 'Recipe profiles',
      description:
        'The recipes you saved from your books, by stage. A new book starts a stage with the default profile of the stage instead of the built-in recipe.',
      loading: 'Loading the profiles…',
      failed: 'The profiles could not be read.',
      empty:
        'You have no profiles yet. Open a recipe in a book and choose “Save as profile” to make one.',
      steps: (steps: number) => `${steps} ${pluralize(steps, 'step', 'steps')}`,
      stepOff: (title: string) => `${title} (off)`,
      defaultBadge: 'Default for new books',
      makeDefault: 'Make default',
      makeDefaultHint: 'A new book starts this stage with this profile',
      unsetDefault: 'Stop being the default',
      unsetDefaultHint: 'A new book starts this stage with the built-in recipe again',
      rename: 'Rename',
      renameTitle: 'Rename the profile',
      renameLabel: 'New name',
      renameSubmit: 'Rename',
      renaming: 'Renaming…',
      remove: 'Delete',
      removeTitle: 'Delete the profile',
      removeDescription: (name: string) =>
        `The profile “${name}” will be deleted from your account. The recipes of the books that were made from it stay as they are.`,
      removeSubmit: 'Delete the profile',
      removing: 'Deleting…',
    },
  },
  processing: {
    loading: 'Loading the steps of this stage…',
    loadFailed: 'The steps of this stage could not be read.',
    recipe: {
      label: 'Recipe',
      active: 'Active',
      option: (name: string, active: boolean, pages: number) =>
        `${name}${active ? ' · active' : ''} · ${pages} ${pluralize(pages, 'page', 'pages')}`,
      activeBadge: (pages: number) => `Active · ${pages} ${pluralize(pages, 'page', 'pages')}`,
      newRecipe: 'New recipe',
      newRecipeHint: 'A copy of this recipe, to try other settings on some pages',
      copyName: (name: string) => `${name} (copy)`,
      use: 'Use this recipe',
      useHint: 'The stage runs by it from now on, and the pages the old one made go out of date',
      choose: 'Recipe of the stage',
      counts: 'Pages by variant',
      countOf: (name: string, pages: number) => `${name} ${pages}`,
    },
    usedFor: {
      title: 'Used for',
      pages: (pages: number) => `Made ${pages} ${pluralize(pages, 'page', 'pages')}`,
      empty: 'No rule sends pages here. A page gets this variant when it is pinned to it.',
      add: 'Add a rule',
      addHint: 'Send every page of a kind to this variant, whenever the stage runs',
      conditions: {
        plates: 'Plates and frontispieces',
        covers: 'Covers',
        blanks: 'Blank pages',
        illustrated: 'Pages with illustrations',
        odd: 'Odd pages',
        even: 'Even pages',
        group: 'Pages of a group',
      } satisfies Record<RuleCondition, string>,
      notYet: 'Takes effect when the Layout stage finds illustrations',
      rule: (condition: string, groupLabel: string) =>
        groupLabel === '' ? condition : `${condition} · ${groupLabel}`,
      remove: (rule: string) => `Remove the rule for ${rule}`,
      groupLabel: 'Name of the group',
      groupAdd: 'Add the rule',
      moves: (rule: string, from: string) => `${rule} go to this variant instead of ${from}`,
      failed: 'The rules could not be read.',
    },
    steps: {
      title: 'Steps',
      step: (number: number, title: string) => `${number} · ${title}`,
      switchLabel: (title: string) => `Run the ${title} step`,
      remove: (title: string) => `Remove the ${title} step`,
      move: (title: string) => `Move the ${title} step`,
      showSettings: (title: string) => `Show the settings of the ${title} step`,
      hideSettings: (title: string) => `Hide the settings of the ${title} step`,
      noSettings: 'This step has no settings.',
      switchedOff: 'Off: the step is kept, but a run and a preview skip it.',
      runThrough: 'Run up to here',
      runThroughHint: (title: string) =>
        `Run the recipe up to the ${title} step, taking the steps before it from the earlier run when nothing changed`,
      runThroughOff: 'Switch this step on, or a step before it, to run up to it.',
      passed: (passed: number, total: number) => `${passed} of ${total} pages passed`,
      passedHint: 'Pages whose result of this stage was made through this step or a later one',
      empty: 'This recipe has no steps. Add one from the list below.',
      add: 'Add a step',
      unknownProcessor: 'This step is not installed on this machine.',
      outOfLimits: 'A value is outside its limits, so the recipe cannot be saved.',
      measure: {
        button: 'Measure the book',
        hint: 'Read the text block and the line height the crop found on every page, and fill in the line height and the page size from their medians, and the margins too while they are measured. The pages of this recipe go out of date.',
        working: 'Measuring…',
        saveFirst: 'Save the recipe before measuring the book.',
        manualMargins:
          'The margins are set by hand, so measuring the book leaves them as they are and sizes the page to hold the median block with them.',
        useMeasured: 'Use measured margins',
        useMeasuredHint:
          'Let the next measure of the book fill in the margins again. Save the recipe, then measure the book.',
      },
      drag: {
        instructions:
          'To pick up a step, press Space. Move it with the arrow keys, drop it with Space, and cancel with Escape.',
        pickedUp: (name: string) => `Picked up the ${name} step.`,
        over: (name: string) => `Over the ${name} step.`,
        dropped: (name: string) => `Dropped the ${name} step.`,
        cancelled: 'Move cancelled. The steps stay in their order.',
      },
    },
    soon: {
      label: 'Soon',
      title: 'Coming steps',
      steps: {
        'geometry.perspective': 'Perspective crop',
        'geometry.dewarp': 'Dewarp by mesh',
        'geometry.crop': 'Crop to the content',
      } satisfies Record<RoadmapKey, string>,
    },
    save: {
      save: 'Save the recipe',
      saving: 'Saving…',
      discard: 'Discard changes',
      unsaved: 'Changes not saved yet.',
      staleWarning: (pages: number) =>
        `Saving makes ${pages} ${pluralize(pages, 'page', 'pages')} out of date.`,
      failed: 'The recipe could not be saved.',
      saveFirst: 'Save the recipe to run it.',
    },
    footer: {
      allClear: 'Every page is up to date.',
      outOfDate: (pages: number) => `${pages} ${pluralize(pages, 'page', 'pages')} out of date`,
      failed: (pages: number) => `${pages} failed`,
      stoppedAt: (step: number, total: number, pages: number) =>
        `Done through step ${step} of ${total}: ${pages} ${pluralize(pages, 'page', 'pages')}`,
      preview: 'Preview this page',
      previewOn: 'Stop the preview',
      previewNoPage: 'Open a page to preview it.',
      previewNoStep: 'Switch on a step that makes a picture to preview it.',
      previewInvalid: 'A value is outside its limits, so there is nothing to preview.',
      previewSplit: 'A cut of a scan into pages has no preview. Try it on a scan instead.',
      run: 'Run',
      busy: 'Another job of this book is still going.',
    },
    scope: {
      menu: 'Run on',
      page: (label: string) => (label === '' ? 'This page' : `This page · ${label}`),
      selected: (count: number) => `Selected pages · ${count}`,
      attention: (count: number) => `Out of date and failed · ${count}`,
      all: (count: number) => `All pages · ${count}`,
    },
    preview: {
      working: 'Making the preview…',
      failed: 'The preview could not be made.',
      busy: 'Another job of this book is running, so the preview waits for it.',
      after: (stage: string) => `After · ${stage} preview`,
    },
    compare: {
      before: (stage: string) => (stage === '' ? 'Before' : `Before · result of ${stage}`),
      after: (stage: string) => `After · ${stage}`,
      afterStep: (stage: string, step: number) => `After · ${stage}, step ${step}`,
      swipe: 'Swipe',
      side: 'Side by side',
      off: 'After only',
      toggle: 'Before / after',
      modeLabel: 'How to compare',
      hold: 'Hold Space to see the page before',
      handle: 'Drag to compare before and after',
      none: 'Nothing comes before this stage, so there is nothing to compare.',
      spreadOnly: 'Comparing works on one page at a time.',
      noImage: 'This page has no picture before or after to compare.',
    },
    thisPage: {
      title: (label: string) => (label === '' ? 'This page' : `This page · ${label}`),
      notProcessed: 'This stage has not run on this page yet.',
      failed: (error: string) =>
        error === '' ? 'The step failed on this page.' : `The step failed: ${error}`,
      outOfDate:
        'This result is out of date: an earlier stage or the recipe changed after it was made.',
      angle: 'Turned by',
      confidence: 'Confidence',
      method: 'Found',
      cut: 'Cut at',
      slant: 'Slant of the cut',
      pages: 'Split into',
      pagesValue: (count: number) => (count === 1 ? 'One page' : 'Two pages'),
      overlap: 'Overlap',
      bend: 'Bend of the lines',
      bendValue: (perThousand: number) => `${perThousand.toFixed(1)} px per 1000 px of width`,
      lines: 'Lines followed',
      degrees: (value: number) => `${value.toFixed(1)}°`,
      pixels: (value: number) => `${Math.round(value)} px`,
      sure: (value: number) => `${value.toFixed(2)} · sure`,
      unsure: (value: number) => `${value.toFixed(2)} · unsure`,
      left: 'Left as it was',
      reviewTitle: {
        'not-applied': 'Left as it was: the step was unsure.',
        'low-confidence': 'The step was not sure of this result.',
        'unsure-gutter':
          'The gutter of this spread was not found for certain, so the cut may be off.',
        'narrow-gutter':
          'This scan is narrower than a spread, yet a gutter runs through its middle. It was kept as one page.',
        'cut-by-edge':
          'The frame of the text comes to a side where the scanner cut the paper, so the margin on that side is not known and the text may be cut.',
        'size-differs':
          'The text of this page differs too much in size from the text of the book, so it was left at its own size.',
        'few-lines':
          'Too few lines of text were found to tell how this page is bent, so it was left as it was.',
        'high-residual':
          'The lines of this page are still bent after dewarping, so the result may be off.',
      },
      reviewHint: 'Set it by hand in the page editor, or change the settings and preview again.',
      stoppedAt: (step: number, total: number) =>
        `Run through step ${step} of ${total} only. The next stage reads this page after the rest is run.`,
      result: {
        label: 'Result shown',
        last: 'Last step',
        option: (number: number, title: string) => `${number} · ${title}`,
        notReached: (number: number) =>
          `This page has not reached step ${number}, so its latest result is shown.`,
      },
      how: 'Method',
      automatic: 'Automatic',
      manual: 'By hand',
      variant: 'Variant',
      pinned: 'Pinned to this page',
      byRules: 'By the rules of the book',
      useRules: "Use the book's rules",
      useRulesHint:
        'Take the pin off, so a run of the stage chooses the variant of this page again',
    },
    apply: {
      menu: (name: string) => `Apply ${name} to…`,
      label: 'Apply to',
      hint: 'Pin the variant you are looking at to pages, or send a whole kind of page to it',
      page: 'This page',
      selected: (count: number) => `Selected pages · ${count}`,
      kind: (kind: string, count: number) => `All pages of this kind · ${kind} · ${count}`,
      noRuleForKind: (kind: string) => `${kind} has no rule of its own`,
      all: (count: number) => `All pages · ${count}`,
      saveFirst: 'Save the recipe before applying it.',
    },
    history: {
      title: 'Results on this page',
      current: 'Current',
      use: 'Use this result',
      using: 'Using…',
      empty: 'Run the stage to make a result.',
      made: (time: string) => `Made ${time}`,
    },
    stale: {
      title: (before: string, verb: string) => `${before} changed after these pages were ${verb}`,
      verbs: {
        'page-split': 'cut',
        geometry: 'straightened',
      } satisfies Partial<Record<Stage, string>>,
      otherVerb: 'processed',
      rerun: (pages: number) => `Run again on ${pages} ${pluralize(pages, 'page', 'pages')}`,
    },
    reasons: {
      atStep: (number: number, title: string, reason: string) =>
        `Step ${number} · ${title}: ${reason}`,
      failed: (error: string) => (error === '' ? 'Failed' : `Failed: ${error}`),
      stale: 'Out of date',
      notApplied: (confidence: number | null) =>
        confidence === null ? 'Left as it was' : `Left as it was · ${confidence.toFixed(2)}`,
      lowConfidence: (confidence: number | null) =>
        confidence === null ? 'Unsure' : `Unsure · ${confidence.toFixed(2)}`,
      unsureGutter: (confidence: number | null) =>
        confidence === null
          ? 'Gutter not found for certain'
          : `Gutter not found for certain · ${confidence.toFixed(2)}`,
      narrowGutter: 'Narrow scan with a gutter in the middle',
      cutByEdge: 'Text may be cut by the edge of the scan',
      sizeDiffers: 'The text of this page differs too much in size',
      fewLines: 'Too few lines to tell how the page is bent',
      highResidual: 'Lines still bent after dewarping',
    },
    split: {
      banner: (wide: number, split: number, toCut: number) => {
        const looks =
          wide === 1
            ? '1 scan is wider than tall and looks like an open book.'
            : `${wide} scans are wider than tall and look like open books.`;
        const next =
          split === 0
            ? `Cut ${toCut === 1 ? 'it' : 'them all'} in one go, then check the cut on ${toCut === 1 ? 'it' : 'each'}.`
            : `${split} ${pluralize(split, 'is', 'are')} split already; cut the other ${toCut} the same way, then check the cut on each.`;
        return `${looks} ${next}`;
      },
      cut: (count: number) => `Split the ${count} ${pluralize(count, 'scan', 'scans')}`,
      notNow: 'Not now',
      scan: 'This scan',
      choice: 'What this scan becomes',
      onePage: 'One page',
      twoPages: 'Two pages',
      noRecipe:
        'The stage has no recipe with the automatic split, so the choice cannot be kept. Add one in the recipes of the stage.',
      automatic: 'The automatic split decides.',
      chosen: (choice: string) => `You chose: ${choice}.`,
      auto: 'Auto',
      confirmOne: {
        title: 'Go back to one page?',
        body: 'The right page of this scan is deleted together with its work, including its number, its kind and the notes written on it. The left page becomes the whole scan again.',
        confirm: 'Go back to one page',
      },
      confirmAuto: {
        title: 'Return to the automatic split?',
        body: 'If the automatic split keeps this scan as one page, the right page is deleted together with its work, including its number, its kind and the notes written on it. The left page becomes the whole scan.',
        confirm: 'Use the automatic split',
      },
      cancel: 'Keep two pages',
    },
  },
  editors: {
    setByHand: 'Set by hand',
    auto: 'Auto',
    autoTitle: 'Go back to what the step finds by itself',
    saving: 'Saving…',
    compareOff: 'Finish setting by hand to compare the page before and after.',
    line: {
      name: 'Split line',
      start: 'Top end of the split line',
      end: 'Bottom end of the split line',
      hint: 'Drag the ends of the dashed line to move the cut, or nudge it with the arrow keys. The new cut is saved at once and the two pages are cut again.',
      noPicture: 'This scan has no picture to draw the cut on yet.',
      // A page without a number is named by its place in the book
      pageName: (label: string, position: number) =>
        label === '' ? `page ${position + 1}` : `p. ${label}`,
      left: (name: string | null) => (name === null ? 'Left half' : `Left · becomes ${name}`),
      right: (name: string | null) => (name === null ? 'Right half' : `Right · becomes ${name}`),
    },
    rotation: {
      name: 'Page rotation',
      handle: 'Rotation handle',
      angle: 'Angle in degrees',
      hint: 'Drag the handle, type the angle, or hold Alt and turn the wheel until the lines of text lie along the guides.',
    },
    quad: {
      name: 'Corners of the sheet',
      corner: (corner: string) => `Corner of the sheet: ${corner}`,
      hint: 'Drag the corners of the green outline to the corners of the paper, or nudge the last one you grabbed with the arrow keys. The page is straightened again at once.',
    },
    rect: {
      name: 'Frame of the content',
      handle: (handle: string) => `Handle of the frame: ${handle}`,
      hint: 'Drag the handles of the blue frame until it holds all the text and the pictures of the page, or nudge it with the arrow keys. The margin is added round it and the page is cut again at once.',
      placement: {
        name: 'Block of text on the page',
        hint: 'Drag the blue frame to where the block of text stands on the page, and its handles to change how large it is, or nudge it with the arrow keys. This page is placed again at once, and Auto goes back to the settings of the step.',
      },
    },
    mesh: {
      name: 'Curves of the lines',
      node: (curve: number, node: number) => `Node ${node} of the curve ${curve}`,
      valueText: (curves: number, nodes: number) => `${curves} curves of ${nodes} nodes each`,
      hint: 'Lay the top curve on the first line of text and the bottom curve on the last, by dragging their nodes, or nudge the node you grabbed last with the arrow keys. The page is flattened between the curves again at once.',
      moreControl: 'More control',
      fewerControls: 'Fewer controls',
    },
    steps: {
      title: 'Steps of this stage',
      kinds: {
        line: 'Split line',
        rotation: 'Angle',
        split: 'Pages',
        quad: 'Sheet corners',
        rect: 'Content frame',
        mesh: 'Page curves',
      },
      placement: 'Block on the page',
      auto: 'auto',
      manual: 'by hand',
    },
  },
} as const;
