import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { PlusIcon } from 'lucide-react';
import { useState } from 'react';
import { createProjectApiV1ProjectsPostMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateProjectList } from '@/features/projects/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The button that opens the form of a new book. Only the title is required, and a created book opens at once, so
 * the next step, uploading its files, is one click away.
 */

const TITLE_MAX_LENGTH = 500;
const SUBTITLE_MAX_LENGTH = 300;

export function CreateProjectDialog(): React.JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [subtitle, setSubtitle] = useState('');

  const create = useMutation({
    ...createProjectApiV1ProjectsPostMutation(),
    onSuccess: async (project) => {
      await invalidateProjectList(queryClient);
      setOpen(false);
      setTitle('');
      setSubtitle('');
      await navigate({ to: '/projects/$projectId', params: { projectId: project.id } });
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <PlusIcon />
          {MESSAGES.projects.create.open}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate({ body: { title, subtitle } });
          }}
        >
          <DialogHeader>
            <DialogTitle>{MESSAGES.projects.create.title}</DialogTitle>
            <DialogDescription>{MESSAGES.projects.create.description}</DialogDescription>
          </DialogHeader>
          <TextField
            label={MESSAGES.projects.create.titleLabel}
            name="title"
            required
            maxLength={TITLE_MAX_LENGTH}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <TextField
            label={MESSAGES.projects.create.subtitleLabel}
            name="subtitle"
            maxLength={SUBTITLE_MAX_LENGTH}
            value={subtitle}
            onChange={(event) => setSubtitle(event.target.value)}
          />
          {create.isError ? <ErrorAlert message={describeError(create.error)} /> : null}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending || title.trim() === ''}>
              {create.isPending
                ? MESSAGES.projects.create.submitting
                : MESSAGES.projects.create.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
