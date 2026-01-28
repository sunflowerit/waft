# Waft project template

This repository is the template project for Waft.

## Creating a new project

You can start a new Waft/Odoo project based on this template by using [copier](https://copier.readthedocs.io).

    copier copy https://github.com/sunflowerit/waft --trust --vcs-ref use-copier myproject
 
This will ask you for the Odoo and waftlib versions to use, then set up the project.
 
Select an Odoo version that you want to use, for example 18.0.

When you are happy, push the project somewhere you like:

```
git init
git remote add git@org:repo.git
git add .
git commit -m "[ADD] initial project set up"
git push
```

## Updating to a newer project template version

If you want to update a project to the newest version of the template, you can use:

```
cd myproject
copier update [--vcs-ref use-copier] --trust
```

Copier will then check back in with `github.com/sunflowerit/waft` and update any files to its newest template, and run migration scripts if they exist.

If any project files have be edited locally, copier will not replace the files but show a conflict that you can solve.

Once happy you can push the updates to your project repository:

```
git add .
git commit -m "[UPD] waft to newer version"
git push
```

## Using waft features

Please see the [main waftlib README](https://github.com/sunflowerit/waftlib).
