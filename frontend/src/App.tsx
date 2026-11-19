import React, { useEffect, useState } from 'react'
import { Navbar } from './components/Navbar'
import { Sidebar } from './components/Sidebar'
import { OverviewPage } from './pages/OverviewPage'
import { CreateReelPage } from './pages/CreateReelPage'
import { DiscoveryPage } from './pages/DiscoveryPage'
import { QueuePage } from './pages/QueuePage'
import { ContentLibraryPage } from './pages/ContentLibraryPage'
import { PublishingQueuePage } from './pages/PublishingQueuePage'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { LogsPage } from './pages/LogsPage'
import { SeoGrowthPage } from './pages/SeoGrowthPage'
import { ShortsIntelligencePage } from './pages/ShortsIntelligencePage'
import { fetchOverview } from './services/api'
import { OverviewData } from './types'

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState('create')
  const [activeMode, setActiveMode] = useState('auto')
  const [overview, setOverview] = useState<OverviewData | null>(null)

  const loadData = async () => {
    try {
      const data = await fetchOverview()
      setOverview(data)
    } catch (e) {
      console.error('Failed to load overview:', e)
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="min-h-screen flex flex-col bg-[#0a0d14] text-slate-100">
      <Navbar overview={overview} activeMode={activeMode} setActiveMode={setActiveMode} />

      <div className="flex-1 flex overflow-hidden">
        <Sidebar
          currentTab={currentTab}
          setCurrentTab={setCurrentTab}
          dailyGenerated={overview?.kpis.today_generated ?? 0}
          targetGeneration={overview?.kpis.daily_generation_target ?? 15}
          dailyPublished={overview?.kpis.today_published ?? (overview?.kpis.published_count ?? 0)}
          targetPublishing={overview?.kpis.daily_publishing_target ?? (overview?.kpis.daily_target ?? 15)}
          queuedCount={overview?.kpis.today_queued ?? (overview?.kpis.pending_queue ?? 0)}
          failedCount={overview?.kpis.today_failed ?? (overview?.kpis.failed_count ?? 0)}
        />

        <main className="flex-1 overflow-y-auto p-8 max-w-7xl mx-auto w-full">
          {currentTab === 'overview' && (
            <OverviewPage data={overview} onNavigate={(tab) => setCurrentTab(tab)} />
          )}
          {currentTab === 'create' && (
            <CreateReelPage onReelCreated={() => setCurrentTab('queue')} activeMode={activeMode} />
          )}
          {currentTab === 'discovery' && (
            <DiscoveryPage onNavigate={(tab) => setCurrentTab(tab)} />
          )}
          {currentTab === 'queue' && <QueuePage />}
          {currentTab === 'published' && <PublishingQueuePage />}
          {currentTab === 'seo' && <SeoGrowthPage />}
          {currentTab === 'library' && <ContentLibraryPage />}
          {currentTab === 'scheduler' && <PublishingQueuePage />}
          {currentTab === 'analytics' && <ShortsIntelligencePage />}
          {currentTab === 'connections' && <ConnectionsPage />}
          {currentTab === 'ai-settings' && <ConnectionsPage />}
          {currentTab === 'logs' && <LogsPage />}
        </main>
      </div>
    </div>
  )
}
