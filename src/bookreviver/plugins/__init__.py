"""Built-in processing plugins, registered through the same entry points as external ones.

A plugin is one step of a recipe. The packages here are the groups the steps belong to, which are the stages of the
pipeline: ``split``, ``pages``, ``geometry`` and ``cleanup``. A group imports ``kit``, which holds what plugins share,
and never another group, so a group can be taken out or replaced without touching the others.
"""
