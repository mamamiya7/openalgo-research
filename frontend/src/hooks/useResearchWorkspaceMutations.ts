import { createContext, useContext } from 'react'

export const ResearchWorkspaceMutations = createContext({
  beforeChange: async (): Promise<void> => {},
  afterChoice: async (): Promise<void> => {},
})

export const useResearchWorkspaceMutations = () => useContext(ResearchWorkspaceMutations)
