/** A registered sample, exactly as returned by the API (`SampleResponse`). */
export interface Sample {
  id: string
  sample_name: string
  material_description: string
  collection_location: string
  intended_application: string
  /** ISO 8601 timestamp in UTC (the backend serialises it with a `Z` suffix). */
  registered_at: string
}

/** The four fields accepted by `POST /api/v1/samples` (`SampleCreate`). */
export type SampleInput = Pick<
  Sample,
  | 'sample_name'
  | 'collection_location'
  | 'material_description'
  | 'intended_application'
>

export type SampleField = keyof SampleInput

/** Response of `GET /health`. */
export interface BackendHealth {
  status: string
  application: string
  version: string
}
