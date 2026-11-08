import { useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'

import { ValidationError } from '../api/client'
import { createSample } from '../api/samples'
import {
  REGISTRATION_UNCERTAIN_MESSAGE,
  describeApiError,
  describeUncertainFailure,
  isOutcomeUncertain,
} from '../lib/errors'
import {
  EMPTY_FORM_VALUES,
  SAMPLE_FIELDS,
  countCharacters,
  validateSampleField,
  validateSampleForm,
} from '../lib/sampleForm'
import type { FieldErrors, SampleFormValues } from '../lib/sampleForm'
import type { Sample, SampleField } from '../types/sample'

interface SampleFormProps {
  /** Called after the API has accepted the registration. */
  onRegistered: (sample: Sample) => void
}

const KNOWN_FIELDS = new Set<string>(SAMPLE_FIELDS.map((field) => field.name))

function isSampleField(name: string): name is SampleField {
  return KNOWN_FIELDS.has(name)
}

/**
 * Form-level banner. While field messages are showing, the banner goes away
 * once the last of them is corrected. A banner about the outcome of a
 * submission (rejected or unconfirmed) never coexists with field messages, so
 * editing the fields leaves it in place until the next submission.
 */
interface FormError {
  message: string
  detail?: string
}

export function SampleForm({ onRegistered }: SampleFormProps) {
  const [values, setValues] = useState<SampleFormValues>(EMPTY_FORM_VALUES)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<FormError | null>(null)
  const [generalIssues, setGeneralIssues] = useState<string[]>([])
  const [success, setSuccess] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  // State updates are asynchronous, so a ref closes the double-submit window.
  const inFlight = useRef(false)

  function focusField(name: SampleField) {
    const control = formRef.current?.elements.namedItem(name)
    if (control instanceof HTMLElement) {
      control.focus()
    }
  }

  function handleChange(
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) {
    const name = event.target.name as SampleField
    const value = event.target.value
    setValues((current) => ({ ...current, [name]: value }))
    setSuccess(null)

    if (fieldErrors[name] === undefined) {
      return
    }
    // A field's message stays until that field is valid again, and follows the
    // text as it is edited. The form-level validation banner is dropped only
    // once no field message (and no form-wide API message) remains.
    const remaining = { ...fieldErrors }
    const message = validateSampleField(name, value)
    if (message === undefined) {
      delete remaining[name]
    } else {
      remaining[name] = message
    }
    setFieldErrors(remaining)
    if (Object.keys(remaining).length === 0 && generalIssues.length === 0) {
      setFormError(null)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current) {
      return
    }
    setSuccess(null)
    setGeneralIssues([])

    const result = validateSampleForm(values)
    if (!result.ok) {
      setFieldErrors(result.errors)
      setFormError({
        message: 'Correct the highlighted fields, then register the sample.',
      })
      const firstInvalid = SAMPLE_FIELDS.find(
        (field) => result.errors[field.name] !== undefined,
      )
      if (firstInvalid) {
        focusField(firstInvalid.name)
      }
      return
    }

    setFieldErrors({})
    setFormError(null)
    inFlight.current = true
    setSubmitting(true)
    try {
      const created = await createSample(result.input)
      setValues(EMPTY_FORM_VALUES)
      setSuccess(
        `Registered “${created.sample_name}”. Its record is shown under Sample detail.`,
      )
      onRegistered(created)
    } catch (error) {
      if (error instanceof ValidationError) {
        const nextFieldErrors: FieldErrors = {}
        const general: string[] = []
        for (const issue of error.issues) {
          if (issue.field !== null && isSampleField(issue.field)) {
            nextFieldErrors[issue.field] ??= issue.message
          } else {
            general.push(issue.message)
          }
        }
        setFieldErrors(nextFieldErrors)
        setGeneralIssues(general)
        setFormError({
          message:
            'The API rejected this registration. Your entries have been kept.',
        })
      } else if (isOutcomeUncertain(error)) {
        // A lost connection or a 5xx can happen after the server stored the
        // sample, so never claim it was (or was not) registered.
        setFormError({
          message: REGISTRATION_UNCERTAIN_MESSAGE,
          detail: `${describeUncertainFailure(error)} Your entries have been kept.`,
        })
      } else {
        // The API answered with a 4xx: the request was refused.
        setFormError({
          message: 'The sample was not registered.',
          detail: `${describeApiError(error)} Your entries have been kept.`,
        })
      }
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return (
    <section className="panel" aria-labelledby="register-heading">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Registration</p>
          <h2 id="register-heading" className="panel__title">
            Register a sample
          </h2>
        </div>
      </div>

      <form ref={formRef} className="form" onSubmit={handleSubmit} noValidate>
        <p className="form__intro">
          All four fields are required. Entries are saved as typed, with
          surrounding spaces removed.
        </p>

        {formError !== null && (
          <div className="form__alert" role="alert">
            <p>{formError.message}</p>
            {formError.detail !== undefined && (
              <p className="form__alert-detail">{formError.detail}</p>
            )}
            {generalIssues.length > 0 && (
              <ul>
                {generalIssues.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {SAMPLE_FIELDS.map((field) => {
          const id = `sample-${field.name}`
          const error = fieldErrors[field.name]
          const hintId = `${id}-hint`
          const errorId = `${id}-error`
          const describedBy =
            [field.hint ? hintId : null, error ? errorId : null]
              .filter(Boolean)
              .join(' ') || undefined
          const count = countCharacters(values[field.name].trim())
          const commonProps = {
            id,
            name: field.name,
            value: values[field.name],
            onChange: handleChange,
            'aria-invalid': error !== undefined,
            'aria-describedby': describedBy,
          }
          return (
            <div
              key={field.name}
              className={`field${error !== undefined ? ' field--invalid' : ''}`}
            >
              <label htmlFor={id}>{field.label}</label>
              {field.multiline ? (
                <textarea {...commonProps} rows={3} />
              ) : (
                <input {...commonProps} type="text" autoComplete="off" />
              )}
              {field.hint && (
                <p id={hintId} className="field__hint">
                  {field.hint}
                </p>
              )}
              <div className="field__foot">
                <p id={errorId} className="field__error">
                  {error}
                </p>
                <p
                  className={`field__count${count > field.maxLength ? ' field__count--over' : ''}`}
                >
                  {count} / {field.maxLength}
                </p>
              </div>
            </div>
          )
        })}

        <div className="form__actions">
          <button type="submit" className="btn btn--primary" disabled={submitting}>
            {submitting ? 'Registering…' : 'Register sample'}
          </button>
        </div>

        {success !== null && (
          <p className="form__success" role="status">
            {success}
          </p>
        )}

        <p className="form__scope">
          Only these four fields are stored. Coordinates, dates, photographs and
          laboratory results cannot be recorded from this form.
        </p>
      </form>
    </section>
  )
}
