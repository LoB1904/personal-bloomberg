import axios from 'axios'
import type {
  ScreenerResponse,
  PaperSignalsResponse,
  TrackRecordResponse,
  StrategyHistoryResponse,
  ScreenerProfile,
  SignalStatus,
} from './types'

const http = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 15_000,
})

export async function getScreener(profile: ScreenerProfile): Promise<ScreenerResponse> {
  const { data } = await http.get<ScreenerResponse>(`/screener/${profile}`)
  return data
}

export async function getPaperSignals(
  profile?: ScreenerProfile,
  status?: SignalStatus,
): Promise<PaperSignalsResponse> {
  const { data } = await http.get<PaperSignalsResponse>('/paper/signals', {
    params: { profile, status },
  })
  return data
}

export async function getTrackRecord(
  profile?: ScreenerProfile,
): Promise<TrackRecordResponse> {
  const { data } = await http.get<TrackRecordResponse>('/paper/track-record', {
    params: { profile },
  })
  return data
}

export async function getStrategyHistory(
  sampleEvery = 5,
): Promise<StrategyHistoryResponse> {
  const { data } = await http.get<StrategyHistoryResponse>('/paper/strategy-history', {
    params: { sample_every: sampleEvery },
  })
  return data
}
